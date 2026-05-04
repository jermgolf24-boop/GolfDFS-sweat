# DFS Mid-Contest Rooting Guide

A Streamlit app for live-tournament leverage analysis. Upload a mid-contest DraftKings standings file and see which players to root for hardest, who's threatening your position from above, and who could pass you from below.

## What it does

Drop a mid-contest standings CSV (or ZIP) → enter your handle → see:

- **Status bar** — distance from your chosen target line (cash / top 1% / win) + status pill
- **Stat cards** — best lineup, median, lineups inside target, total holes remaining across portfolio
- **Players to root for** — every meaningful leverage play, sorted by leverage × ceiling. Direction (root for / against) and mechanical "Why" description for each.
- **Cut players (collapsed)** — leverage no longer matters once they're out
- **Above-me threats** — entries scoring above your best lineup, with overlap and holes-remaining
- **Below-me threats** — entries within range that could pass you on a hot finish

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

