# DFS Mid-Contest Rooting Guide

A Streamlit app for live-tournament leverage analysis. Upload a mid-contest DraftKings standings file and see which players to root for hardest, who's threatening your position from above, and who could pass you from below.

## What it does

Drop a mid-contest standings CSV (or ZIP) → enter your handle → see (across three tabs):

**Tab 1 — Players to root for**: Every meaningful leverage play, sorted by leverage × ceiling. Direction (root for / against) and mechanical "Why" description for each.

**Tab 2 — Threats**: Above-me and below-me entry lists with overlap and holes-remaining.

**Tab 3 — Local leverage** (the sharpest tab): For each player, count appearances in **all** lineups above your best lineup vs lineups closest below. Net lev = your exposure − Above %.

- Positive Net lev = your portfolio overweights this player vs the lineups you need to catch. A hot day from them moves you up, not the field above.
- Negative Net lev = the lineups ahead of you are heavier on this player than you are. Their hot day pushes those lineups further from reach.

Field ownership averages over all 35K entries. Local leverage measures the lineups that actually affect your relative finish. **Cadillac case study from this app:** Cam Young Net lev was −80.2 (you 17%, lineups above you 97.6%). Min Woo Lee Net lev +16 (you 23%, lineups above you 7%). The two best Cam Young alternatives we missed (Adam Scott at −54.6, Sepp Straka at −24.7) were the structural levers we needed.

**Tab 4 — Lineup deep-dive**: Per-lineup card analysis for your top N lineups by current score. With DataGolf in-play data enabled (paid API), each card shows:

- Current rank, score, gap to target, holes remaining
- Per-player breakdown: current FPTS, FPTS rank, status, win/top-5/top-10/top-20 probabilities
- "Expected remaining" per player: probability-weighted total FPTS minus current FPTS
- Lineup status badge: ALIVE (realistic ceiling beats target) or LOCKED OUT (mathematically dead)

Locked-out lineups are exactly that — even with everything going right, they can't catch the target. Stop rooting for them and reallocate attention to the ALIVE ones.

## DataGolf in-play setup (Tab 4 only)

The Lineup deep-dive tab uses DataGolf's `/preds/in-play` endpoint to compute realistic ceilings. This requires a paid DataGolf API tier with in-play access.

**To enable on Streamlit Cloud:**

1. Open your app in [share.streamlit.io](https://share.streamlit.io)
2. Click "Settings" (the gear icon, usually bottom-right of the app management page)
3. Click "Secrets"
4. Add this line:
   ```toml
   DATAGOLF_API_KEY = "your_api_key_here"
   ```
5. Save. Streamlit Cloud auto-restarts the app within 30 seconds.
6. In the sidebar, check "Use DataGolf in-play data".

**To enable locally:**

Create `.streamlit/secrets.toml` in your project directory:
```toml
DATAGOLF_API_KEY = "your_api_key_here"
```

The key never enters your GitHub repo this way — it's stored in Streamlit's secrets manager.

**When in-play data is unavailable** (no live tournament, network issue, key invalid), the deep-dive cards still render but without ceiling estimates. Status shows "NO CEILING DATA".

## Conservative ceiling math

The realistic ceiling per player is `max(0, expected_total_FPTS − current_FPTS)`. For a player already exceeding their expected total (hot round in progress), this returns 0 even though they could go higher. This is intentionally conservative — DataGolf's probabilities reflect "where they'll finish," not "additional upside from a ceiling round on top of an already-hot round." A locked-out flag should be trusted; an alive flag may be slightly understating ceiling for hot players.

## How cut status is inferred

DraftKings exports lineup-level holes-remaining in mid-contest standings. A lineup with one cut player has 18 fewer holes remaining than otherwise-identical lineups.

For each player, the app compares the average holes-remaining of lineups containing that player vs lineups without them. A delta of −10 or more flags the player as cut. This is most accurate at round boundaries (between R3 and R4) and gets noisier mid-round.

If the standings export doesn't include holes-remaining (post-tournament exports often don't), the cut inference falls back to "active" for everyone and the app still works for raw leverage analysis — you just lose the cut-status filter on the rooting table.

**Validated on synthetic mid-contest data:** 5/5 cuts correctly identified, 0 false positives.

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Cloud

Same path as the postmortem analyzer:

1. New GitHub repo with `app.py` and `requirements.txt`
2. share.streamlit.io → New app → point at the repo
3. Deploy. ~2 minute build.

## Workflow

**During a tournament:**
1. After R2 (Friday evening) — upload standings to see how your portfolio survived the cut
2. After R3 (Saturday evening) — re-upload, this is the most actionable moment for "who do I need to root for tomorrow"
3. During R4 (Sunday) — re-upload every few hours to track live as players play

**Status pill thresholds:**
- **On pace** — at least one of your lineups is already inside the target line
- **Needs gain** — closest lineup is within 25 points of target
- **Must make move** — closest lineup is more than 25 points back

## What's NOT in this version

- **DataGolf live in-play model** — would give actual top-X probabilities for each remaining player. Currently uses FPTS rank as a ceiling-path proxy.
- **Live auto-refresh** — must re-upload manually
- **Player-level holes remaining** — DK only exports lineup-level. We infer per-player cut status; we don't infer per-player holes-played.
- **Score projection** — "if Si Woo finishes T5 you'll move to top 1%" requires modeling each remaining hole's expected impact. Beyond v1 scope.

## Roadmap

- v2: DataGolf in-play integration (paid tier)
- v3: Live auto-refresh on a configurable cadence
- v4: Combine with the postmortem analyzer into one multi-page app

