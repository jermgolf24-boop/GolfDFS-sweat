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
import json
import re
from urllib.request import urlopen
from urllib.error import URLError, HTTPError
from collections import defaultdict, Counter


# =============================================================================
# DataGolf in-play integration
# =============================================================================
# Default expected FPTS by finish tier on a typical PGA classic. These are tunable
# in the sidebar — tournaments with longer fields or richer finish bonuses score
# higher; opposite-field events lower. Defaults calibrated to recent classics.
DEFAULT_TIER_FPTS = {
    'win': 130,        # Win bonus + dominant scoring round
    'top_5': 100,      # Strong finish bonus + above-average scoring
    'top_10': 80,
    'top_20': 60,
    'remaining_field': 35,  # Made cut, no top-20 → finish bonus only
}

NAME_OVERRIDES_DG_TO_DK = {
    # DG returns "first last" with various accent handling; DK uses preferred display name
    'matt fitzpatrick': 'Matt Fitzpatrick',
    'matthew mccarty': 'Matthew McCarty',
    'nicolas echavarria': 'Nicolas Echavarria',
}


def norm_name(s):
    """Normalize: lowercase, strip non-alpha. Used for cross-source matching."""
    return re.sub(r'[^a-z]', '', str(s).lower())


@st.cache_data(ttl=300, show_spinner=False)  # 5-minute TTL matches API refresh cadence
def fetch_datagolf_inplay(api_key, tour='pga'):
    """Fetch live in-play finish probabilities from DataGolf.

    Returns dict: {normalized_player_name: {'win_pct': X, 'top_5_pct': X,
                                              'top_10_pct': X, 'top_20_pct': X,
                                              'display_name': 'Original Name'}}

    Returns None on any failure (no key, network error, no live tournament).
    """
    if not api_key:
        return None
    url = (
        f"https://feeds.datagolf.com/preds/in-play"
        f"?tour={tour}&dead_heat=no&odds_format=percent"
        f"&file_format=json&key={api_key}"
    )
    try:
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError):
        return None

    # The endpoint returns an object; player array is typically under 'data' or 'players'.
    # Different DG endpoints use different schemas; we handle both.
    players = data.get('data') or data.get('players') or []
    if not players:
        return None

    out = {}
    for p in players:
        # Field names per DG docs: player_name, win, top_5, top_10, top_20
        # Some endpoints return values as percentages already (e.g. 12.5), others as
        # decimals (0.125). We auto-detect via magnitude.
        name = p.get('player_name') or p.get('name')
        if not name:
            continue
        win = _coerce_pct(p.get('win'))
        t5 = _coerce_pct(p.get('top_5'))
        t10 = _coerce_pct(p.get('top_10'))
        t20 = _coerce_pct(p.get('top_20'))
        out[norm_name(name)] = {
            'display_name': name,
            'win_pct': win,
            'top_5_pct': t5,
            'top_10_pct': t10,
            'top_20_pct': t20,
        }
    return out


def _coerce_pct(v):
    """Convert a DataGolf probability value to percent (0-100). DG sometimes returns
    decimals (0.125) and sometimes percent values (12.5)."""
    if v is None or pd.isna(v):
        return 0.0
    try:
        f = float(v)
    except (ValueError, TypeError):
        return 0.0
    return f * 100 if f <= 1.0 else f


def expected_remaining_fpts(probs, tier_fpts):
    """Given a player's in-play probabilities and tier-FPTS assumptions,
    compute expected total final FPTS via tier decomposition.

    Tiers are nested (top-5 ⊂ top-10 ⊂ top-20), so we decompose into
    mutually-exclusive bands:
      - win                       : prob = win
      - top 5 but not winner      : prob = top_5 - win
      - top 10 but not top 5      : prob = top_10 - top_5
      - top 20 but not top 10     : prob = top_20 - top_10
      - made cut, outside top 20  : prob = 100 - top_20  (assumes player is alive;
                                              for cut players this returns 0 via probs)
    """
    win = probs.get('win_pct', 0) / 100
    t5 = probs.get('top_5_pct', 0) / 100
    t10 = probs.get('top_10_pct', 0) / 100
    t20 = probs.get('top_20_pct', 0) / 100

    p_win = win
    p_top5_not_win = max(0, t5 - win)
    p_top10_not_top5 = max(0, t10 - t5)
    p_top20_not_top10 = max(0, t20 - t10)
    p_field = max(0, 1.0 - t20)

    expected = (
        p_win * tier_fpts['win']
        + p_top5_not_win * tier_fpts['top_5']
        + p_top10_not_top5 * tier_fpts['top_10']
        + p_top20_not_top10 * tier_fpts['top_20']
        + p_field * tier_fpts['remaining_field']
    )
    return expected

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
    st.markdown("**Lineup deep-dive**")
    deep_dive_n = st.slider(
        "Top N lineups to analyze",
        min_value=1, max_value=10, value=5, step=1,
        help="Per-lineup ceiling analysis is shown for your top N lineups by current score.",
    )

    use_datagolf = st.checkbox(
        "Use DataGolf in-play data",
        value=False,
        help="Pulls live finish probabilities from DataGolf for ceiling estimates. Requires API key in Streamlit secrets.",
    )

    if use_datagolf:
        with st.expander("Tier FPTS assumptions (advanced)"):
            st.caption("Expected DK FPTS at each finish tier. Defaults calibrated to a typical PGA classic.")
            tier_win = st.number_input("Win", value=DEFAULT_TIER_FPTS['win'], step=5)
            tier_t5 = st.number_input("Top 5 (not win)", value=DEFAULT_TIER_FPTS['top_5'], step=5)
            tier_t10 = st.number_input("Top 10 (not top 5)", value=DEFAULT_TIER_FPTS['top_10'], step=5)
            tier_t20 = st.number_input("Top 20 (not top 10)", value=DEFAULT_TIER_FPTS['top_20'], step=5)
            tier_field = st.number_input("Made cut, outside top 20", value=DEFAULT_TIER_FPTS['remaining_field'], step=5)
            tier_fpts_user = {
                'win': tier_win, 'top_5': tier_t5, 'top_10': tier_t10,
                'top_20': tier_t20, 'remaining_field': tier_field,
            }
    else:
        tier_fpts_user = DEFAULT_TIER_FPTS

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


def compute_local_leverage(entries_df, my_best_lineup, my_best_score, my_handle,
                            my_exposures, n_my_lineups, players_df,
                            cut_status, n_below=100, exclude_my_handle=True):
    """For each player, count appearances in lineups above my best vs lineups
    closest below my best.

    Excludes my own lineups by default (otherwise our own exposures pollute
    the 'above me' counts on plays we made well).

    Returns DataFrame sorted by net leverage descending.
    """
    my_set = my_best_lineup['set']

    above_lineups = []
    below_lineups = []

    for _, r in entries_df.iterrows():
        full_name = str(r.get('EntryName', ''))
        handle = full_name.split(' (')[0].strip()
        if exclude_my_handle and handle == my_handle:
            continue
        lineup = parse_lineup(r.get('Lineup'))
        if len(lineup) != 6:
            continue
        pts = float(r['Points']) if pd.notna(r['Points']) else 0.0
        if pts > my_best_score:
            above_lineups.append(set(lineup))
        elif pts < my_best_score:
            below_lineups.append((pts, set(lineup)))

    # Cap below at n_below closest to my score
    below_lineups.sort(key=lambda x: -x[0])
    below_capped = [s for _, s in below_lineups[:n_below]]

    n_above = len(above_lineups)
    n_below_actual = len(below_capped)

    # Tally player appearances
    above_count = Counter()
    below_count = Counter()
    for lu in above_lineups:
        for p in lu:
            above_count[p] += 1
    for lu in below_capped:
        for p in lu:
            below_count[p] += 1

    field_own = dict(zip(players_df['Player'], players_df['field_own']))
    fpts = dict(zip(players_df['Player'], players_df['FPTS']))
    fpts_rank = {p: i + 1 for i, p in enumerate(
        players_df.sort_values('FPTS', ascending=False)['Player'].tolist()
    )}

    rows = []
    for player in players_df['Player']:
        my_pct = my_exposures.get(player, 0) / n_my_lineups * 100 if n_my_lineups else 0
        above_pct = above_count[player] / n_above * 100 if n_above else 0
        below_pct = below_count[player] / n_below_actual * 100 if n_below_actual else 0
        net_lev = my_pct - above_pct
        rows.append({
            'Player': player,
            'Status': cut_status.get(player, 'unknown'),
            'My %': my_pct,
            'Above %': above_pct,
            'Below %': below_pct,
            'Net lev': net_lev,
            'My − Below': my_pct - below_pct,
            'Field %': field_own.get(player, 0),
            'FPTS': fpts.get(player, 0),
            'FPTS rank': fpts_rank.get(player, 999),
        })

    return pd.DataFrame(rows), n_above, n_below_actual


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

# ---- Tabbed analysis ----
tab1, tab2, tab3, tab4 = st.tabs([
    "Players to root for", "Threats", "Local leverage", "Lineup deep-dive"
])

with tab1:
    player_stats = compute_player_field_stats(my_handle, user_exposures, user_lineups, players_df)
    rooting_df = build_rooting_table(player_stats, cut_status, len(players_df))

    display_rooting = rooting_df[rooting_df['Lev pp'].abs() >= 1].copy()
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

with tab2:
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

with tab3:
    n_my = len(user_lineups[my_handle])
    my_exp = user_exposures[my_handle]

    local_lev_df, n_above_total, n_below_actual = compute_local_leverage(
        entries_df,
        summary['best_lineup'],
        summary['best_score'],
        my_handle,
        my_exp,
        n_my,
        players_df,
        cut_status,
        n_below=100,
        exclude_my_handle=True,
    )

    st.caption(
        f"For each player, count appearances in **all {n_above_total:,} lineups above your best** "
        f"vs the **{n_below_actual} lineups closest below**. **Net lev** = your exposure − Above %. "
        "Positive Net lev means a hot day from this player helps you more than it helps the lineups "
        "you need to catch. Your own lineups are excluded from the counts."
    )

    if n_above_total == 0:
        st.success(
            "Nobody is above your best lineup, so Local leverage doesn't apply right now. "
            "The 'Below me' counts are still informative for defending your position."
        )

    # Show all players with non-trivial my % or above %
    relevant = local_lev_df[
        (local_lev_df['My %'] >= 1) | (local_lev_df['Above %'] >= 5)
    ].copy()

    # Drop cut players from primary view
    active = relevant[relevant['Status'] != 'cut'].copy()
    cut = relevant[relevant['Status'] == 'cut'].copy()

    # Sort by Net lev descending — most-helpful-if-they-pop at top
    active = active.sort_values('Net lev', ascending=False).reset_index(drop=True)

    display_cols = ['Player', 'Status', 'My %', 'Above %', 'Below %', 'Net lev',
                    'My − Below', 'Field %', 'FPTS rank']
    styled = (
        active[display_cols].style
        .format({
            'My %': '{:.1f}',
            'Above %': '{:.1f}',
            'Below %': '{:.1f}',
            'Net lev': '{:+.1f}',
            'My − Below': '{:+.1f}',
            'Field %': '{:.1f}',
        })
        .map(style_lev, subset=['Net lev'])
        .map(style_status, subset=['Status'])
    )
    st.dataframe(styled, use_container_width=True, height=min(500, 60 + 35 * len(active)))

    if len(cut):
        with st.expander(f"Cut players ({len(cut)}) — leverage no longer matters"):
            st.dataframe(
                cut[display_cols].style.format({
                    'My %': '{:.1f}', 'Above %': '{:.1f}', 'Below %': '{:.1f}',
                    'Net lev': '{:+.1f}', 'My − Below': '{:+.1f}', 'Field %': '{:.1f}',
                }),
                use_container_width=True,
            )

    csv = active[display_cols].to_csv(index=False).encode('utf-8')
    st.download_button("⬇ Download CSV", csv, f"{my_handle}_local_leverage.csv", "text/csv")

with tab4:
    st.caption(
        f"Per-lineup ceiling analysis for your top {deep_dive_n} lineups by current score. "
        "For each, see which players are still alive, their realistic remaining FPTS based "
        "on DataGolf in-play probabilities, and whether the lineup has a mathematical path "
        "to your target line."
    )

    # ---- Try to fetch DataGolf in-play data ----
    dg_probs = None
    dg_status_msg = None
    if use_datagolf:
        api_key = None
        try:
            api_key = st.secrets.get('DATAGOLF_API_KEY')
        except (FileNotFoundError, KeyError):
            api_key = None

        if not api_key:
            dg_status_msg = (
                "⚠ DataGolf API key not found in Streamlit secrets. "
                "Add `DATAGOLF_API_KEY = \"your_key\"` to .streamlit/secrets.toml "
                "(local) or your app's Secrets settings (Streamlit Cloud)."
            )
        else:
            with st.spinner("Fetching DataGolf in-play probabilities..."):
                dg_probs = fetch_datagolf_inplay(api_key)
            if dg_probs is None:
                dg_status_msg = (
                    "⚠ DataGolf in-play fetch failed. Possible reasons: no live "
                    "tournament right now, network issue, or invalid API key. "
                    "Falling back to current FPTS only (no ceiling estimates)."
                )
            else:
                dg_status_msg = f"✓ DataGolf in-play data loaded ({len(dg_probs)} players)."

    if dg_status_msg:
        st.caption(dg_status_msg)

    # ---- Get top N lineups by current score ----
    my_lineups = sorted(
        user_lineups[my_handle], key=lambda l: -l['points']
    )[:deep_dive_n]

    if not my_lineups:
        st.info(f"No lineups found for handle '{my_handle}'.")
    else:
        # Pre-compute per-player joins for cards
        fpts_lookup = dict(zip(players_df['Player'], players_df['FPTS']))
        fpts_rank_lookup = {
            p: i + 1 for i, p in enumerate(
                players_df.sort_values('FPTS', ascending=False)['Player'].tolist()
            )
        }
        n_field_players = len(players_df)

        for lineup_idx, lu in enumerate(my_lineups, 1):
            current_score = lu['points']
            current_rank = lu['rank']
            holes_remaining = lu.get('holes_remaining')

            # ---- Per-player breakdown ----
            player_rows = []
            sum_remaining_ceiling = 0
            ceiling_available = dg_probs is not None
            for p in lu['players']:
                cur_fpts = fpts_lookup.get(p, 0)
                rank = fpts_rank_lookup.get(p, n_field_players)
                p_status = cut_status.get(p, 'unknown')
                row = {
                    'Player': p,
                    'Status': p_status,
                    'Current FPTS': cur_fpts,
                    'FPTS rank': rank,
                }
                if ceiling_available:
                    probs = dg_probs.get(norm_name(p))
                    if probs and p_status != 'cut':
                        expected_total = expected_remaining_fpts(probs, tier_fpts_user)
                        remaining = max(0, expected_total - cur_fpts)
                        row['Expected remaining'] = remaining
                        row['Win %'] = probs.get('win_pct', 0)
                        row['Top 5 %'] = probs.get('top_5_pct', 0)
                        row['Top 10 %'] = probs.get('top_10_pct', 0)
                        row['Top 20 %'] = probs.get('top_20_pct', 0)
                        sum_remaining_ceiling += remaining
                    else:
                        row['Expected remaining'] = 0.0 if p_status == 'cut' else None
                        row['Win %'] = None
                        row['Top 5 %'] = None
                        row['Top 10 %'] = None
                        row['Top 20 %'] = None
                player_rows.append(row)

            realistic_ceiling = current_score + sum_remaining_ceiling if ceiling_available else None
            gap_to_target = max(0, target_line - current_score)
            ceiling_gap = realistic_ceiling - target_line if realistic_ceiling is not None else None

            # ---- Card header ----
            if ceiling_available and ceiling_gap is not None:
                if ceiling_gap >= 0:
                    card_status_label = "ALIVE"
                    card_status_color = "#173404"
                    card_status_bg = "#C0DD97"
                else:
                    card_status_label = "LOCKED OUT"
                    card_status_color = "#501313"
                    card_status_bg = "#F7C1C1"
                ceiling_text = f"Realistic ceiling {realistic_ceiling:.1f}"
            else:
                card_status_label = "NO CEILING DATA"
                card_status_color = "#412402"
                card_status_bg = "#FAEEDA"
                ceiling_text = "Enable DataGolf in-play for ceiling analysis"

            st.markdown(
                f"""
                <div style="border: 0.5px solid var(--secondary-background-color, #e6e6e6);
                            border-radius: 8px; padding: 16px; margin: 16px 0 8px 0;">
                  <div style="display: flex; justify-content: space-between; align-items: baseline;
                              gap: 12px; flex-wrap: wrap; margin-bottom: 8px;">
                    <div>
                      <span style="font-weight: 500; font-size: 16px;">Lineup #{lineup_idx}</span>
                      <span style="color: #888; margin-left: 12px; font-size: 14px;">
                        Rank #{current_rank:,} · {current_score:.1f} pts
                        · {gap_to_target:.1f} from {target_label} line
                      </span>
                    </div>
                    <span style="background: {card_status_bg}; color: {card_status_color};
                                  padding: 2px 10px; border-radius: 999px; font-size: 12px;
                                  font-weight: 500;">{card_status_label}</span>
                  </div>
                  <div style="font-size: 13px; color: #666;">
                    {ceiling_text} · {holes_remaining if holes_remaining is not None else '—'} holes remaining
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # ---- Per-player table ----
            pdf = pd.DataFrame(player_rows)
            if ceiling_available:
                fmt_cols = {
                    'Current FPTS': '{:.1f}',
                    'Expected remaining': '{:.1f}',
                    'Win %': '{:.1f}',
                    'Top 5 %': '{:.1f}',
                    'Top 10 %': '{:.1f}',
                    'Top 20 %': '{:.1f}',
                }
                styled = (
                    pdf.style.format(fmt_cols, na_rep='—')
                    .map(style_status, subset=['Status'])
                )
            else:
                styled = (
                    pdf.style.format({'Current FPTS': '{:.1f}'})
                    .map(style_status, subset=['Status'])
                )
            st.dataframe(styled, use_container_width=True, hide_index=True)

# ---- Footer ----
st.markdown("---")
st.caption(
    "Cut status inferred from lineup-level holes-remaining patterns. Most accurate at "
    "round boundaries (between R3 and R4) — mid-round inference is noisier. "
    "Re-upload a fresh standings file to refresh."
)

