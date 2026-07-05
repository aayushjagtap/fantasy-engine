# DARKO Access & Projection Backbone — Decision (M0.5)

## What we checked

- **darko.app** is live (the old shiny app went offline June 2026; this is the new
  home, maintained by Kostya, frontend by Andrew Patton). Pages: leaderboard,
  projections, trajectories, lineups, standings, etc.
- **No documented public API or CSV/JSON export.** The site is a JavaScript app
  that renders data client-side. Pulling live projections would require
  browser-rendered scraping or reverse-engineering internal data endpoints —
  fragile, site-specific, and a maintenance burden that breaks whenever they
  change their frontend.
- **Historical DARKO data IS available as CSV** via `anpatton/basic-nba-tutorials`
  (Andrew Patton — same person who builds the darko.app frontend). Verified file:
  `team_ratings/data/player_ratings_minutes.csv`.

## Two findings from pulling a real DARKO file

1. **DARKO CSVs carry `nba_id`** (the NBA person ID). So the DARKO ↔ nba_api join
   is a direct integer match — *no name-matching needed*. The M0 normalization
   work is only required for Sleeper.
2. **The accessible CSV is DPM + minutes, not the box-score line.** Columns are
   `nba_id, player_name, team_name, o_dpm, d_dpm, minutes` — an *impact* metric
   plus projected minutes. The full projected box score (pts/reb/ast/stl/blk/
   fg3m/fg%/ft%/tov) that the value engine needs lives behind the JS app with no
   clean export.

## Decision: the v1 projection backbone

Earlier plan was "consume DARKO's box-score projections directly." The access
reality changes that. Revised plan:

- **v1 projection input = a transparent baseline we compute from nba_api**
  (recent-seasons weighted average + aging curve, per `VALUE_ENGINE.md`). Fully
  unblocked, no external fragility, and we control it end to end.
- **DARKO = benchmark, not dependency.** In the backtest, compare our rankings and
  buy-low/sell-high flags against DARKO-informed ones where we can.
- **DARKO box-score projections = a drop-in upgrade later**, if/when we solve the
  export (live scrape, or a published sheet if one exists). The value engine takes
  "a projected line" as input, so the *source* of that line is swappable — nothing
  downstream cares where it came from.
- **DARKO DPM + minutes (accessible now) = a usable opportunity signal.** Projected
  minutes feed value directly; DPM can inform role/context.

**Why this is fine — arguably better.** We're not rebuilding DARKO's science or a
fantasy value engine that already exists; we're building the league-adaptive value
*layer*, which is the actual differentiator. A projection baseline we build
ourselves and then *benchmark against DARKO* is a stronger "I built it and measured
it" story than pure dependence on a source we can't cleanly access — and it keeps
us moving instead of stuck on brittle scraping.

## Canonical keys (crosswalk, updated)

- Canonical player key = **`nba_id`**.
- **DARKO** → joins on `nba_id` directly (it's in the data).
- **Sleeper** → joins via normalized name → `nba_id` (the M0 spike logic; ~1 in 20
  needs a manual override, and remember "retired / not in this source's roster" is
  a *scope* miss, not a spelling miss).

## Next

- **M1:** ingest nba_api box scores (realized stats) and design the projection
  baseline.
- **Deferred:** darko.app live box-score-projection export — revisit after the
  engine and backtest exist and we know exactly what shape of projection we need.
