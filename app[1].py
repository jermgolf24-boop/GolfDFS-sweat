"""
DFS Mid-Contest Rooting Guide
==============================
Upload a mid-contest DK standings file. For your handle, see:
  - Where you stand vs cash / top 1% / win lines
  - Players to root for (or against) ranked by leverage × ceiling
  - Threats above (entries you need to catch) and below (entries that could pass you)
    weighted by remaining-variance (holes left in their portfolio)

All calculations driven by:
  - Player FPTS (current scoring)
  - Lineup-level holes-remaining (per DK export)
  - Cut status inferred from holes-remaining deltas

Run locally:
    streamlit run app.py
"""
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO, StringIO
import zipfile
from collections import defaultdict, Counter

# =============================================================================
# Page config
# =============================================================================
st.set_page_config(
    page_title="DFS Rooting Guide",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.title("Settings")
    my_handle = st.text_input(
        "Your DK handle",
        value=st.session_state.get('my_handle', ''),
        help="Identifies your portfolio in the standings.",
    )
    if my_handle:
        st.session_state['my_handle'] = my_handle

    st.markdown("---")
    st.markdown("**Tournament context**")

    target_pct = st.radio(
        "Optimize for",
        options=['Cash (top 10%)', 'Top 1%', 'Win'],
        index=1,
        help="Which contest line your strategy targets. Affects rooting recommendations.",
    )

    st.markdown("---")
    st.markdown("**Threat list sizes**")
    threats_above_n = st.slider(
        "Above me",
        min_value=10, max_value=200, value=50, step=10,
    )
    threats_below_n = st.slider(
        "Below me",
        min_value=10, max_value=300, value=100, step=10,
    )

    st.markdown("---")
    st.markdown("**About**")
    st.caption(
        "Re-upload a fresh standings file as the tournament progresses to refresh "
        "the analysis. All processing is local — nothing is stored or transmitted."
    )

    if st.session_state.get('contest_loaded'):
        st.markdown("---")
        if st.button("Clear and upload different file"):
            for k in list(st.session_state.keys()):
                if k.startswith(('contest', 'entries', 'players', 'user_', 'field_',
                                  'fpts', 'winner', 'holes', 'cut_')):
                    del st.session_state[k]
            st.rerun()


# =============================================================================
# CSV parsing
# =============================================================================
def parse_lineup(s):
    if pd.isna(s):
        return []
    s = str(s).strip()
    if s.startswith('G '):
        s = s[2:]
    return [p.strip() for p in s.split(' G ') if p.strip()]


def parse_holes_remaining(val):
    """Holes remaining is exported in various formats. Try to coerce to int."""
    if pd.isna(val) or val == '':
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


@st.cache_data(show_spinner=False)
def load_standings(file_bytes, filename):
    """Read a CSV or ZIP. Returns (entries_df, players_df)."""
    if filename.lower().endswith('.zip'):
        with zipfile.ZipFile(BytesIO(file_bytes)) as z:
            csv_names = [n for n in z.namelist() if n.lower().endswith('.csv')]
            if not csv_names:
                raise ValueError("No CSV found inside ZIP.")
            with z.open(csv_names[0]) as f:
                csv_text = f.read().decode('utf-8-sig')
    else:
        csv_text = file_bytes.decode('utf-8-sig')

    df = pd.read_csv(StringIO(csv_text))

    entries = df.dropna(subset=['Rank']).copy()
    entries['Rank'] = entries['Rank'].astype(int)
    entries['Points'] = pd.to_numeric(entries['Points'], errors='coerce')

    # Holes remaining can be in different columns depending on contest state
    holes_col = None
    for candidate in ['HolesRemaining', 'Holes Remaining', 'TimeRemaining', 'Time Remaining']:
        if candidate in entries.columns:
            holes_col = candidate
            break
    if holes_col:
        entries['holes_remaining'] = entries[holes_col].apply(parse_holes_remaining)
    else:
        entries['holes_remaining'] = None

    players = df.dropna(subset=['Player']).copy()
    players['FPTS'] = pd.to_numeric(players['FPTS'], errors='coerce')
    if '%Drafted' in players.columns:
        players['field_own'] = (
            players['%Drafted'].astype(str).str.rstrip('%').astype(float)
        )
    else:
        players['field_own'] = 0.0
    players = players[['Player', 'field_own', 'FPTS']].copy()

    return entries, players


@st.cache_data(show_spinner=False)
def build_user_data(entries_df):
    """Per-user lineup tally with holes remaining tracked."""
    user_lineups = defaultdict(list)
    user_exposures = defaultdict(Counter)

    for _, row in entries_df.iterrows():
        full_name = str(row.get('EntryName', ''))
        handle = full_name.split(' (')[0].strip()
        if not handle:
            continue
        lineup = parse_lineup(row.get('Lineup'))
        if len(lineup) != 6:
            continue
        lu_set = frozenset(lineup)
        holes = row.get('holes_remaining')
        user_lineups[handle].append({
            'players': lineup,
            'set': lu_set,
            'points': float(row['Points']) if pd.notna(row['Points']) else 0.0,
            'rank': int(row['Rank']),
            'holes_remaining': int(holes) if holes is not None and not pd.isna(holes) else None,
        })
        for p in lineup:
            user_exposures[handle][p] += 1
    return dict(user_lineups), dict(user_exposures)


@st.cache_data(show_spinner=False)
def infer_cut_status(entries_df, all_players):
    """For each player, infer cut status from holes-remaining deltas across the field.

    Method: for each player P, compute average holes-remaining of lineups containing P
    vs lineups not containing P. If P is cut, lineups containing P will systematically
    have ~18 fewer holes remaining (for the relevant round) than lineups without P,
    holding everything else equal.

    Caveats:
      - Most accurate at round boundaries (R3→R4 transition). Mid-round noisier.
      - Returns 'unknown' if holes_remaining isn't in the export.
    """
    if 'holes_remaining' not in entries_df.columns or entries_df['holes_remaining'].isna().all():
        return {p: 'unknown' for p in all_players}

    # Build flat (lineup_holes, player_set) table
    rows = []
    for _, r in entries_df.iterrows():
        h = r.get('holes_remaining')
        if h is None or pd.isna(h):
            continue
        lineup = parse_lineup(r.get('Lineup'))
        if len(lineup) != 6:
            continue
        rows.append({'holes': int(h), 'players': set(lineup)})

    if not rows:
        return {p: 'unknown' for p in all_players}

    # Quick lookup: per player, list of holes_remaining values for lineups containing them
    holes_with = defaultdict(list)
    holes_without = defaultdict(list)
    all_set = set(all_players)
    for r in rows:
        for p in all_set:
            if p in r['players']:
                holes_with[p].append(r['holes'])
            else:
                holes_without[p].append(r['holes'])

    status = {}
    cut_threshold = -10  # 18-hole signal degraded by other variance ≈ -8 to -12
    active_threshold = -2

    for p in all_players:
        with_vals = holes_with.get(p, [])
        without_vals = holes_without.get(p, [])
        if not with_vals or not without_vals:
            status[p] = 'unknown'
            continue
        with_avg = sum(with_vals) / len(with_vals)
        without_avg = sum(without_vals) / len(without_vals)
        delta = with_avg - without_avg
        if delta < cut_threshold:
            status[p] = 'cut'
        elif delta > active_threshold:
            status[p] = 'active'
        else:
            status[p] = 'ambiguous'
    return status


# =============================================================================
# Analysis functions
# =============================================================================
def quantile(arr, q):
    s = sorted(arr)
    if not s:
        return 0
    pos = (len(s) - 1) * q
    base = int(pos)
    rest = pos - base
    if base + 1 < len(s):
        return s[base] + rest * (s[base + 1] - s[base])
    return s[base]


def compute_user_summary(handle, user_lineups, all_scores, target_line):
    lineups = user_lineups[handle]
    n = len(lineups)
    scores = sorted([lu['points'] for lu in lineups], reverse=True)
    ranks = [lu['rank'] for lu in lineups]
    best_lineup = max(lineups, key=lambda l: l['points'])

    # Total holes remaining across portfolio (proxy for upside potential)
    total_holes = sum((lu['holes_remaining'] or 0) for lu in lineups)
    has_holes = any(lu['holes_remaining'] is not None for lu in lineups)

    in_target = sum(1 for s in scores if s >= target_line)
    median_idx = len(scores) // 2
    median_score = scores[median_idx] if scores else 0
    median_rank = sorted(ranks)[len(ranks) // 2] if ranks else 0

    gap = max(0, target_line - max(scores)) if scores else 0
    if max(scores) >= target_line:
        status = 'on_pace'
    elif gap < 25:
        status = 'needs_gain'
    else:
        status = 'must_move'

    return {
        'n_lineups': n,
        'best_score': max(scores) if scores else 0,
        'best_rank': min(ranks) if ranks else 0,
        'best_lineup': best_lineup,
        'median_score': median_score,
        'median_rank': median_rank,
        'in_target': in_target,
        'gap_to_target': gap,
        'status': status,
        'total_holes_remaining': total_holes,
        'has_holes_data': has_holes,
    }


def compute_player_field_stats(handle, user_exposures, user_lineups, players_df):
    """For each player, compute my exposure and field exposure."""
    n_my = len(user_lineups[handle])
    field_own = dict(zip(players_df['Player'], players_df['field_own']))
    fpts = dict(zip(players_df['Player'], players_df['FPTS']))
    fpts_rank = {p: i + 1 for i, p in enumerate(
        players_df.sort_values('FPTS', ascending=False)['Player'].tolist()
    )}

    rows = []
    my_exp = user_exposures[handle]
    for player in players_df['Player']:
        my_pct = my_exp.get(player, 0) / n_my * 100
        field_pct = field_own.get(player, 0)
        rows.append({
            'Player': player,
            'My %': my_pct,
            'Field %': field_pct,
            'Lev pp': my_pct - field_pct,
            'FPTS': fpts.get(player, 0),
            'FPTS rank': fpts_rank.get(player, 999),
        })
    return pd.DataFrame(rows)


def build_rooting_table(player_stats_df, cut_status, n_field_players):
    """Compute the rooting recommendation table.

    leverage_score = abs(Lev pp) × ceiling_indicator
    where ceiling_indicator is 0 if cut, scaled if not in contention.

    Direction: 'root for' if My % > Field %, 'root against' if My % < Field %.
    """
    df = player_stats_df.copy()
    df['Status'] = df['Player'].map(lambda p: cut_status.get(p, 'unknown'))

    # Ceiling indicator: 0 if cut, full weight if active and in top 30, half weight if active outside top 30
    def ceiling_factor(row):
        s = row['Status']
        if s == 'cut':
            return 0.0
        if row['FPTS rank'] <= 30:
            return 1.0
        if row['FPTS rank'] <= 60:
            return 0.5
        return 0.2

    df['Ceiling factor'] = df.apply(ceiling_factor, axis=1)
    df['Leverage score'] = df['Lev pp'].abs() * df['Ceiling factor']
    df['Direction'] = df['Lev pp'].apply(
        lambda v: 'Root for' if v > 0 else ('Root against' if v < 0 else 'Neutral')
    )

    # Mechanical "Why" descriptions
    def why(row):
        s = row['Status']
        if s == 'cut':
            return 'Cut — no path remaining'
        rank = int(row['FPTS rank'])
        lev = row['Lev pp']
        my = row['My %']
        field = row['Field %']
        if abs(lev) < 1:
            return f'No leverage (you {my:.0f}%, field {field:.0f}%)'
        rank_phrase = f'currently FPTS #{rank}' if rank <= 30 else f'FPTS #{rank}, far from contention'
        if lev > 0:
            return f'You +{lev:.1f}pp ({my:.0f}% vs field {field:.0f}%); {rank_phrase}'
        else:
            return f'Field +{abs(lev):.1f}pp ({field:.0f}% vs your {my:.0f}%); {rank_phrase}'

    df['Why'] = df.apply(why, axis=1)
    return df.sort_values('Leverage score', ascending=False).reset_index(drop=True)


def compute_threats(entries_df, my_best_lineup, my_best_score, n_above, n_below):
    """Threat lists: above me and below me, weighted by holes-remaining and overlap."""
    my_set = my_best_lineup['set']

    # All entries with their holes remaining and overlap with my best lineup
    rows = []
    for _, r in entries_df.iterrows():
        lineup = parse_lineup(r.get('Lineup'))
        if len(lineup) != 6:
            continue
        their_set = frozenset(lineup)
        overlap = len(my_set & their_set)
        full_name = str(r.get('EntryName', ''))
        handle = full_name.split(' (')[0].strip()
        rows.append({
            'Rank': int(r['Rank']),
            'Handle': handle,
            'Points': float(r['Points']) if pd.notna(r['Points']) else 0,
            'Holes': r.get('holes_remaining'),
            'Overlap': overlap,
            'Overlap %': overlap / 6 * 100,
            'Lineup': lineup,
        })

    # Threat score:
    # - Above me: lower is better for them. Weight = holes_remaining × (1 - overlap/6)
    #   Threats with low overlap and many holes are stickier above.
    # - Below me: weight = holes_remaining × (1 - overlap/6).
    #   Threats with low overlap and many holes can pass me through divergent ceilings.
    above = []
    below = []
    for r in rows:
        if r['Points'] > my_best_score:
            above.append(r)
        elif r['Points'] < my_best_score:
            below.append(r)

    # Sort: above by closest-above (smallest gap), below by closest-below
    above_sorted = sorted(above, key=lambda x: x['Points'])
    below_sorted = sorted(below, key=lambda x: -x['Points'])

    return above_sorted[:n_above], below_sorted[:n_below]


# =============================================================================
# Display helpers
# =============================================================================
def style_lev(v):
    if pd.isna(v):
        return ''
    if v >= 5:
        return 'background-color: #C0DD97; color: #173404;'
    if v <= -5:
        return 'background-color: #F7C1C1; color: #501313;'
    return ''


def style_status(v):
    if v == 'cut':
        return 'background-color: #D3D1C7; color: #2C2C2A;'
    if v == 'active':
        return ''
    if v == 'ambiguous':
        return 'background-color: #FAEEDA; color: #412402;'
    return 'color: #888780;'


def style_direction(v):
    if v == 'Root for':
        return 'color: #173404; font-weight: 500;'
    if v == 'Root against':
        return 'color: #501313; font-weight: 500;'
    return ''


# =============================================================================
# Main app
# =============================================================================
st.title("🎯 DFS rooting guide")
st.caption("Mid-contest leverage analysis: who to root for, plus threat lists above and below you")

# ---- Upload ----
if not st.session_state.get('contest_loaded'):
    st.markdown("### Upload mid-contest standings")
    uploaded = st.file_uploader(
        "DraftKings contest standings file (CSV or ZIP)",
        type=['csv', 'zip'],
        help="Re-export from DK during the tournament for fresh analysis. Holes Remaining column enables cut-status inference.",
    )
    if uploaded:
        try:
            with st.spinner(f"Parsing {uploaded.name}..."):
                file_bytes = uploaded.read()
                entries_df, players_df = load_standings(file_bytes, uploaded.name)
                user_lineups, user_exposures = build_user_data(entries_df)
                cut_status = infer_cut_status(entries_df, players_df['Player'].tolist())

            st.session_state['entries_df'] = entries_df
            st.session_state['players_df'] = players_df
            st.session_state['user_lineups'] = user_lineups
            st.session_state['user_exposures'] = user_exposures
            st.session_state['cut_status'] = cut_status
            st.session_state['contest_loaded'] = True
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")
    st.stop()

entries_df = st.session_state['entries_df']
players_df = st.session_state['players_df']
user_lineups = st.session_state['user_lineups']
user_exposures = st.session_state['user_exposures']
cut_status = st.session_state['cut_status']

# ---- Validate handle ----
if not my_handle:
    st.warning("Set your DK handle in the sidebar.")
    st.stop()
if my_handle not in user_lineups:
    available = sorted(user_lineups.keys())[:10]
    st.error(
        f"Handle '{my_handle}' not found in this contest. "
        f"Sample handles: {', '.join(available)}..."
    )
    st.stop()

# ---- Compute target line ----
all_scores = entries_df['Points'].dropna().tolist()
if target_pct.startswith('Cash'):
    target_line = quantile(all_scores, 0.90)
    target_label = 'cash'
elif target_pct.startswith('Top 1%'):
    target_line = quantile(all_scores, 0.99)
    target_label = 'top 1%'
else:
    target_line = max(all_scores) if all_scores else 0
    target_label = 'win'

summary = compute_user_summary(my_handle, user_lineups, all_scores, target_line)

# ---- Status bar ----
status_text = {
    'on_pace': ('On pace', '#C0DD97', '#173404'),
    'needs_gain': ('Needs gain', '#FAC775', '#412402'),
    'must_move': ('Must make move', '#F7C1C1', '#501313'),
}
label, bg, fg = status_text[summary['status']]

n_active_cut = sum(1 for v in cut_status.values() if v == 'cut')
n_active_active = sum(1 for v in cut_status.values() if v == 'active')

contest_meta = (
    f"{len(entries_df):,} entries · "
    f"{n_active_active} active / {n_active_cut} cut" + (
        " (inferred from holes remaining)" if n_active_cut > 0 else ""
    )
)
st.caption(contest_meta)

st.markdown(
    f"""
    <div style="display: flex; gap: 12px; align-items: center; padding: 12px 16px; 
                background: {bg}; color: {fg}; border-radius: 8px; margin-bottom: 1rem;">
      <span style="background: {fg}; color: white; padding: 2px 10px; border-radius: 999px; 
                   font-size: 12px; font-weight: 500;">{label}</span>
      <span style="font-size: 14px;">
        Best lineup <b>#{summary['best_rank']:,}</b> at <b>{summary['best_score']:.1f}</b> pts ·
        <b>{summary['gap_to_target']:.1f} pts</b> from {target_label} line
        ({target_line:.1f}) · <b>{summary['in_target']}/{summary['n_lineups']}</b> currently inside
      </span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---- Stat cards ----
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Lineups", summary['n_lineups'])
c2.metric("Best", f"{summary['best_score']:.1f}",
          help=f"Rank #{summary['best_rank']:,}")
c3.metric("Median", f"{summary['median_score']:.1f}",
          help=f"Rank #{summary['median_rank']:,}")
c4.metric(f"In {target_label}", summary['in_target'])
if summary['has_holes_data']:
    c5.metric("Holes remaining", f"{summary['total_holes_remaining']:,}",
              help="Across your full portfolio. Higher = more upside variance.")
else:
    c5.metric("Holes data", "Not in export",
              help="Mid-contest export needed for holes-remaining analysis.")

# ---- Players to root for ----
st.markdown("## Players to root for")

player_stats = compute_player_field_stats(my_handle, user_exposures, user_lineups, players_df)
rooting_df = build_rooting_table(player_stats, cut_status, len(players_df))

# Filter to meaningful leverage only (>= 1pp absolute)
display_rooting = rooting_df[rooting_df['Lev pp'].abs() >= 1].copy()
# And drop cut players from primary view
active_rooting = display_rooting[display_rooting['Status'] != 'cut'].copy()
cut_rooting = display_rooting[display_rooting['Status'] == 'cut'].copy()

st.caption(
    "Players where your exposure differs meaningfully from the field, ranked by leverage × ceiling factor. "
    "Direction tells you whether to root for them (you're overweight) or against (field is overweight)."
)

display_cols = ['Player', 'Direction', 'My %', 'Field %', 'Lev pp', 'Status', 'FPTS rank', 'Why']
styled = (
    active_rooting[display_cols].style
    .format({'My %': '{:.1f}', 'Field %': '{:.1f}', 'Lev pp': '{:+.1f}'})
    .map(style_lev, subset=['Lev pp'])
    .map(style_direction, subset=['Direction'])
    .map(style_status, subset=['Status'])
)
st.dataframe(styled, use_container_width=True, height=min(500, 60 + 35 * len(active_rooting)))

if len(cut_rooting):
    with st.expander(f"Cut players ({len(cut_rooting)}) — leverage no longer matters"):
        st.dataframe(
            cut_rooting[display_cols].style
            .format({'My %': '{:.1f}', 'Field %': '{:.1f}', 'Lev pp': '{:+.1f}'}),
            use_container_width=True,
        )

# ---- Threat lists ----
st.markdown("## Threats")
above, below = compute_threats(
    entries_df, summary['best_lineup'], summary['best_score'],
    threats_above_n, threats_below_n,
)

col_above, col_below = st.columns(2)

with col_above:
    st.markdown(f"### Above me ({len(above)})")
    st.caption("Entries scoring above your best. Lower overlap with your lineup = harder to catch.")
    if above:
        rows = []
        for r in above:
            rows.append({
                'Rank': r['Rank'],
                'Handle': r['Handle'][:18],
                'Points': r['Points'],
                'Gap': r['Points'] - summary['best_score'],
                'Overlap': f"{r['Overlap']}/6",
                'Holes left': r['Holes'] if r['Holes'] is not None else '—',
            })
        adf = pd.DataFrame(rows)
        styled_above = adf.style.format({
            'Points': '{:.1f}', 'Gap': '+{:.1f}',
        })
        st.dataframe(styled_above, use_container_width=True, height=400)
    else:
        st.success("Nobody is above your best lineup.")

with col_below:
    st.markdown(f"### Below me ({len(below)})")
    st.caption("Entries scoring below your best. Lower overlap + more holes = real threat to pass you.")
    if below:
        rows = []
        for r in below:
            rows.append({
                'Rank': r['Rank'],
                'Handle': r['Handle'][:18],
                'Points': r['Points'],
                'Gap': r['Points'] - summary['best_score'],
                'Overlap': f"{r['Overlap']}/6",
                'Holes left': r['Holes'] if r['Holes'] is not None else '—',
            })
        bdf = pd.DataFrame(rows)
        styled_below = bdf.style.format({
            'Points': '{:.1f}', 'Gap': '{:.1f}',
        })
        st.dataframe(styled_below, use_container_width=True, height=400)
    else:
        st.info("No threats currently below you in range.")

# ---- Footer ----
st.markdown("---")
st.caption(
    "Cut status inferred from lineup-level holes-remaining patterns. Most accurate at "
    "round boundaries (between R3 and R4) — mid-round inference is noisier. "
    "Re-upload a fresh standings file to refresh."
)
