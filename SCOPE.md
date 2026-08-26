# Fantasy Basketball Value Engine — v1 Scope

**Thesis.** A league-adaptive fantasy basketball valuation engine that stands on
top of best-in-class projection systems (DARKO now, EPM later) instead of
reinventing them — turning raw skill projections into punt-aware category value,
and surfacing interpretable buy-low / sell-high signals.

**v1 in one sentence.** Ingest box-score + DARKO data, compute league-adaptive
category values from a config object, and output a ranked draft board with
buy-low / sell-high flags, validated against one historical backtest.

Everything below serves that one sentence. If a feature doesn't help ship it,
it's v2+.

---

## In scope (v1)

- **Ingestion + cache.** Pull player box-score game logs (`nba_api`) and DARKO
  projections for a fixed set of seasons. Every network pull is cached to disk,
  keyed by endpoint + date, so nothing hits a rate limit twice.
- **Player-ID crosswalk.** A reliable join across sources (NBA person IDs ↔ DARKO
  name keys ↔ Sleeper IDs), with name normalization plus a manual override map
  for stragglers.
- **League config object.** The `LeagueConfig` contract (see `league_config.py`).
  Must express 9-category, points, and punt-build leagues.
- **Value engine.** Per-category z-scores against a replacement baseline, summed
  into a value, parameterized entirely by the config. Ratio categories (FG%, FT%)
  are volume-weighted, not naively z-scored on the percentage.
- **Draft board.** A ranked player list that re-sorts when the config changes.
- **Buy-low / sell-high flags.** Compare each player's *current* production to
  their DARKO true-skill projection; flag large divergences (hot = sell-high,
  cold = buy-low). This is the leap/regression feature, done honestly as a signal
  layer on top of DARKO rather than a rival projection.
- **Backtest harness.** One held-out historical season; measure ranking quality
  against a baseline.
- **Surface: CLI only.** Prints the board and writes a CSV. No web app, no
  extension yet.

## Out of scope (deferred to v2+)

Browser extension / draft-room overlay · trade analyzer · in-season streaming,
matchup, and waiver tools · Monte Carlo matchup sim · EPM integration (paid API)
· Yahoo/ESPN league integration · on/off & lineup data (`pbpstats`) · natural-
language / LLM assistant · auction values · keeper/dynasty multi-year value.

*(ADP-based value-gap / sleeper detection via Sleeper is an optional stretch
within v1 — include it only if the core slice lands with time to spare.)*

---

## Success criteria (the three gates)

1. **Reproducible pipeline.** A clean checkout runs end-to-end from raw pull to
   ranked board with a single command.
   > **Status (2026-08-25): met.** `data/cache/` is committed; `pytest -q`
   > (122 tests) and every entry point (`python -m cli.board …`,
   > `python backtest/validate.py`, `engine/*.py`) run fully offline off the
   > cache, no network attempted. CI (`.github/workflows/tests.yml`) runs the
   > same on every push.
2. **Adaptivity proven.** Swapping the config (9-cat → points → punt-FT%)
   re-sorts the board in the expected direction — e.g. a punt-FT% build promotes
   high-volume, low-FT% bigs.
   > **Status (2026-08-25): met.** `python -m cli.board --compare
   > leagues/standard_9cat.json leagues/punt_ft.json` puts the low-FT% big
   > (`giannis_like` in the fixture; real bigs on cached data) in the risers
   > section; `test_compare_promotes_low_ft_big` locks it. Config is data
   > (`leagues/*.json`), not code.
3. **Measured against a baseline.** On a held-out season, the ranking beats the
   chosen baseline on the chosen metric, AND flagged sell-high players regress
   more often than not. Define these up front:
   - **Baseline:** preseason ADP (or a DARKO-naive ranking).
   - **Metric:** rank correlation (Spearman) between preseason rank and
     end-of-season realized value.
   - **Directional check:** share of sell-high flags whose rest-of-season
     production fell toward their projection.
   > **Status (2026-08-25): partially met, with an amendment and an open piece.**
   > *Metric, met but thin:* on the 2025-26 holdout (n = 358) the projection
   > scores Spearman **0.689** vs **0.679** for the baseline actually
   > implemented — **naive persistence, not preseason ADP**. That is a
   > **+0.010** edge; honestly, that is a thin margin, roughly the width of the
   > role-trend ablation itself (+0.013 ON vs OFF), and well short of a
   > decisive result.
   > *Baseline, amended:* preseason ADP was never ingested (the Sleeper/ADP
   > stretch stayed out of v1). `backtest/validate.py` uses naive persistence
   > instead. Treat the baseline as amended to "DARKO-naive / last-season
   > persistence"; pulling real ADP is still the honest bar and remains open
   > (see `BUILD_PLAN.md` → Still open).
   > *Directional check: not done.* Sell-high/buy-low is M5, not started — no
   > flags exist to test regression against yet.

The backtest is the resume-critical piece — "I built it *and proved it*." It is
designed in before coding because it dictates what data gets collected.

---

## Data sources (v1)

| Need | Source | Access | Notes |
|---|---|---|---|
| Box-score stats + game logs | `nba_api` | Python lib; needs headers + rate limiting | Feeds the value calc |
| Projections / true skill | DARKO | Scrape daily CSVs (darko.app); historical CSVs via `anpatton/basic-nba-tutorials` | Consume, don't rebuild |
| Usage / minutes / role | `nba_api` advanced box scores | Same lib | DARKO also ships projected minutes |
| Historical seasons (backtest) | `nba_api` + DARKO historical CSVs | lib + files | 3–5 seasons |
| Schedule / game dates | `nba_api` or balldontlie | free | Aligns projections to games |
| ADP *(optional stretch)* | Sleeper | Sleeper public API (free) | Also the v2 league-integration path |

Prototyping tip: balldontlie (free JSON) is fine for a first join to prove the
pipeline before wrestling with `nba_api` rate limits.

---

## Architecture

Keep the **engine a clean, importable, testable module** with no knowledge of any
surface. The CLI (and, later, the extension or web app) is a thin consumer that
imports the engine. This is what lets multiple front-ends share one core.

```
ingest/      pulls + caches raw data (nba_api, darko), writes to local store
crosswalk/   player-ID reconciliation
engine/      LeagueConfig + value calc + buy-low/sell-high  (the core IP)
backtest/    harness + baseline comparison
cli/         thin surface: run pipeline, print/CSV the board
data/        sqlite + cached raw pulls (gitignored)
```

Storage: SQLite to start (local, zero-config; Postgres only if you outgrow it).

---

## Build order (milestones)

- **M0 — De-risk: player-ID crosswalk spike.** Pull ~20 players from each source,
  prove they join. If this is shaky, everything downstream is.
- **M1 — Ingestion + cache** for one season (box scores + DARKO).
- **M2 — Config schema + loader** (`league_config.py`, already drafted).
- **M3 — Value engine**: z-scores, replacement baseline, ratio-cat weighting, punts.
- **M4 — Draft board CLI**: ranked output + CSV, re-sorts on config swap.
- **M5 — Buy-low / sell-high flags** from current-vs-projection divergence.
- **M6 — Backtest harness** + baseline comparison on a held-out season.

Two milestones can invalidate everything else, so they come first in spirit:
**M0 (crosswalk)** and locking the **v1 scope** above. M2/M3 design can proceed
in parallel with M0.

---

## Decisions to confirm before M1

- How many seasons of history? (proposed: 3–5)
- Replacement-baseline definition — top-N by position, scaled by `num_teams` and
  roster slots? (this is a value-engine design choice; nail it in M3)
- Which season is the backtest holdout?
- Include the optional Sleeper/ADP stretch in v1, or hold for v2?
