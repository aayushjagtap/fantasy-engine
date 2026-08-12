# BUILD_PLAN.md — path from current state to a usable 2026-27 draft tool

Written 2026-08-11. Companion to `SCOPE.md` (which defines *what* v1 is).
This file defines *what to build next and in what order*. Claude Code should
read this and `SCOPE.md` before making changes.

---

## Current state

| Milestone | Status |
|---|---|
| M0 crosswalk spike | Done (`crosswalk/spike.py`). No override map file exists yet. |
| M0.5 DARKO decision | Done (`DARKO_NOTES.md`). DARKO = benchmark, not dependency. |
| M1 ingest | Done (`ingest/nba_boxscores.py`). Season lines, not game logs. |
| M2 config | Done (`engine/league_config.py`). Hardcoded constructors only. |
| M3 value engine | Done (`engine/value.py`) + `engine/diagnose.py` (bonus). |
| Projection | Done (`engine/projection.py`). Not in original milestone list. |
| M4 draft board CLI | **Missing.** No `cli/`, no CSV output. |
| M5 buy-low / sell-high | **Missing.** |
| M6 backtest | Done (`backtest/validate.py`), with a role-trend ablation. |

Also missing: `requirements.txt`, `README.md`, any test runner, the
`crosswalk/` override map, and any way to define a league without editing Python.

---

## 2026-27 season facts (verified 2026-08-11)

- 30 teams, 82 games. **No expansion** — nothing structural changes in the engine.
- Regular season opens Tue Oct 20, 2026. Full schedule released Thu Aug 13, 2026.
- 2025-26 season ended Apr 12, 2026; Finals concluded June 2026. That data is final.
- 2026 draft: June 23-24, 60 picks. AJ Dybantsa (WAS) #1, Darryn Peterson (UTA) #2.
- Heavy player movement this offseason (LeBron James → Philadelphia, a Giannis
  Antetokounmpo trade, others).

**Date logic is already correct** and needs no change: as of today
`recent_completed_seasons(3)` → `["2025-26", "2024-25", "2023-24"]` and
`_next_season_label("2025-26")` → `"2026-27"`. Add a test pinning this to today's
date so it can't silently drift.

---

## P0 — BLOCKER: stats.nba.com blocks datacenter IPs

`stats.nba.com` sits behind Akamai bot protection and **silently drops
connections from datacenter IPs (AWS, GCP, Azure)**. GitHub Codespaces runs on
Azure. `ingest/nba_boxscores.py` will therefore fail inside the Codespace, and it
will fail as a hang/timeout or empty response rather than a clean error.

**Do this before anything else:**

1. On a **local machine (home IP)**, run `python ingest/nba_boxscores.py` to
   populate `data/cache/` with the last 4 completed seasons
   (2025-26, 2024-25, 2023-24, 2022-23).
2. Change `.gitignore` to ignore `data/` **except** `data/cache/`:
   ```
   data/*
   !data/cache/
   ```
3. Commit the cached JSON (~1-2 MB for four seasons — fine in git).
4. Add a loud, actionable error in `_fetch_league_dash` when a network pull is
   attempted and fails: explain the datacenter-IP block and point the user at the
   committed cache, rather than surfacing a bare timeout.
5. Optionally add `--offline` to force cache-only and fail fast if a key is missing.

Result: engine, projection, backtest, CLI, and CI all run fully offline in the
Codespace. This is desirable regardless of the IP issue.

---

## Phase A — Foundation and correctness

Small, unblocks everything, and makes Success Gate #1 ("clean checkout runs
end-to-end with a single command") actually true.

### A1. Packaging and docs
- `requirements.txt` or `pyproject.toml`: `pydantic>=2`, `nba_api`, `requests`,
  `pytest`. Pin major versions.
- `README.md`: what this is, how to install, the exact commands to run, and a
  prominent note about the local-pull-then-commit-cache workflow from P0.
- One entry-point command that runs the whole pipeline end to end.

### A2. Real test suite
The five `--selftest` blocks are good tests trapped in `__main__` guards where CI
can't reach them. Move the logic into `tests/` under pytest. Keep the
`--selftest` flags working as thin wrappers so the existing UX doesn't break.
- Extract the duplicated six-player synthetic fixture (currently copy-pasted
  verbatim between `engine/value.py` and `engine/diagnose.py`) into
  `tests/fixtures.py`.
- Add a GitHub Actions workflow running pytest. It must pass with **no network**,
  which the committed cache makes possible.

### A3. Bug: `SCALE_FIELDS` is incomplete
In `engine/value.py`:
```python
SCALE_FIELDS = ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fga", "fta")
```
`fgm`, `ftm`, and `min` are missing. Many real points leagues score FGM/FTM
directly (the classic FGM +2 / FGA -1 / FTM +1 / FTA -1 shape). Under
`basis="total"` those stats don't receive the availability factor while every
other stat does, so a volume scorer's `fga`/`fta` penalties scale but their
`fgm`/`ftm` credits don't. The board is quietly wrong for a whole class of league.

Fix: add the missing fields. Add a regression test using a `point_values` map
that includes `fgm`/`fga`/`ftm`/`fta` — the current `points_league()` fixture
doesn't exercise this path, which is why the bug is invisible today.

### A4. Bug: backtest compares predictors on different player sets
In `backtest/validate.py`, `corr()` computes its own `common` intersection per
prediction, so `base_c`, `off_c`, and `on_c` are each measured on a **different
population** and then differenced. Since `project_players` only projects players
present in the most recent season while the baseline board is that season's
actuals, the sets genuinely diverge.

Fix: compute one intersection across `actual ∩ baseline ∩ ours_on ∩ ours_off` up
front, score all three on it, and print the N. Success Gate #3 depends on this
number being honest.

### A5. Naming: `basis="total"` doesn't mean totals
With `AVAIL_ALPHA = 0.5`, `"total"` is per-game × √GP, not season totals. The
behavior is correct and worth keeping; the name and docstring are misleading.
Rename the concept to **availability-adjusted** throughout (which is already what
`projection.py`'s demo output calls it). Keep `"total"` accepted as an alias so
nothing breaks.

---

## Phase B — M4: make it usable by a human

This is the step that turns "my engine" into "a tool."

### B1. Config as data, not code
`standard_9cat()` / `punt_ft_9cat()` / `points_league()` are hardcoded Python.
`league_config.py`'s own docstring promises that supporting a new league means
constructing a new config, not editing engine code — for a real user that has to
mean a file.

- `LeagueConfig.load(path)` reading JSON (and YAML if `pyyaml` is acceptable),
  via `model_validate`. Pydantic already gives validation and clear errors.
- `leagues/` directory with worked examples: `standard_9cat.json`,
  `punt_ft.json`, `punt_ast_fg.json`, `points_espn.json`, `roto_8cat.json`.
- Keep the Python constructors as test fixtures.

### B2. The CLI
`cli/board.py`:
```
python -m cli.board --league leagues/standard_9cat.json --top 150 --csv out/board.csv
python -m cli.board --league leagues/punt_ft.json --basis per_game
python -m cli.board --explain "Victor Wembanyama" --league leagues/standard_9cat.json
```
- Prints the ranked board; `--csv` writes it.
- `--explain` routes to the existing `engine/diagnose.py` — that module is
  already the best thing in the repo for user trust, it just has no front door.
- `--compare leagues/a.json leagues/b.json` prints the movers between two
  configs. This *is* Success Gate #2, so make it a first-class command rather
  than a demo buried in `_demo()`.

---

## Phase C — Positions and 2026-27 correctness

### C1. Ingest positions and teams
`FIELD_MAP` has no position or team. Add them (LeagueDashPlayerStats may not
carry position — if not, join from `nba_api.stats.static.players` or
`CommonPlayerInfo`, and cache it). Without this, `RosterSlot.position` is
decorative and replacement level can't be positional.

### C2. Position-aware replacement level
`pool_size = num_teams * total_roster` treats the roster as one flat pool. For a
draft board this materially misranks: center scarcity is half of why draft boards
differ from raw value boards. Implement replacement level per roster slot,
respecting multi-position eligibility (G/F/UTIL). This changes VOR numbers, so
land it **before** Phase D rather than after.

Open decision — pick one and write it down: flat replacement (current), strict
per-position, or a hybrid where UTIL/BENCH slots draw from the flat pool.

### C3. Rookies
`project_players` only projects players who appeared in the most recent season,
so the entire 2026 draft class is absent from the board. For a draft tool that is
a visible failure — Dybantsa and Peterson will be drafted in real leagues.

v1 fix: `data/rookies_2026.json`, a hand-entered projected per-game line for the
top ~15-20 picks, merged in by `project_players` and flagged
`is_rookie: true` so the board can mark them and the backtest can exclude them.
Low-tech and honest beats absent.

### C4. Role overrides for offseason movement
`_role_trend_mult` reads a minutes trajectory and cannot know a player changed
teams. After an offseason this active (LeBron → Philadelphia, the Giannis trade,
etc.) it will project fading roles for players who just landed larger ones.

Add `data/role_overrides.json` — `{nba_id: multiplier}`, hand-edited, applied
after `_role_trend_mult`, printed in `--explain` output so an override is never
silent. Same escape-hatch philosophy as the crosswalk override map.

### C5. Ship the crosswalk override map
`crosswalk/spike.py` recommends a `{normalized_key -> canonical_id}` override
file that doesn't exist. Create it (even if nearly empty) and load it in the
normalizer, so the spike becomes a real module rather than a one-off script.

---

## Phase D — M5 and the roster-aware layer

**Route decision required before starting this phase.** Two orderings:

- **D-first (matches SCOPE.md):** buy-low/sell-high, then team-aware tools.
- **E-first (recommended):** `engine/team.py` first, then buy-low/sell-high.

Rationale for E-first: a ranked list is a solved problem that every fantasy site
already ships. The draft assistant and trade analyzer both require the same
missing object — a roster's current per-category standing — and that object is
the genuinely differentiated part. Cost: M5 slips, and `SCOPE.md` calls M5 the
differentiator.

### D1. M5 — buy-low / sell-high
Divergence between current-season production and the projected line. Report in
units a user understands (rank delta, or per-category z delta), and reuse
`diagnose.py`'s per-category machinery to explain *why* a player is flagged.
Note: this is an in-season feature and needs current-season data, which won't
exist until after Oct 20, 2026. Build it against 2025-26 as a simulated
"in-season" dataset so it can be tested now.

### E1. `engine/team.py` — roster state
A `Team` object holding drafted players and exposing per-category standing
relative to the league. Then:
- **Draft assistant:** given my roster + available players, who most improves my
  weakest *contested* categories? (Marginal value, not raw VOR.)
- **Trade analyzer:** a trade cannot be evaluated by summing VOR in a category
  league. A third elite shot-blocker is worth less to a team already winning
  blocks; a punt-FT% team should happily give away a 90% free-throw shooter.
  Evaluate by change in expected category wins for **both** sides.
- **Punt detection:** infer which categories a roster has effectively already
  punted, and re-rank accordingly.

---

## Open decisions to make before Phase C

1. Replacement baseline: flat, per-position, or hybrid? (See C2.)
2. Is the Sleeper/ADP stretch in v1? It's the natural baseline for Success Gate
   #3 — `SCOPE.md` names preseason ADP as the baseline, but `validate.py`
   currently uses naive persistence instead. Either pull ADP or amend the gate.
3. Which season is the backtest holdout? Currently defaults to the latest
   completed season (2025-26).
4. How many seasons of history? Currently 3 via `RECENCY_WEIGHTS`;
   `SCOPE.md` proposed 3-5.
5. Route decision: D-first or E-first (see Phase D).

---

## Working agreement for Claude Code

- The engine stays surface-agnostic. Nothing in `engine/` may import from `cli/`.
- Every new module ships with tests in `tests/`, runnable offline.
- No network calls in tests or CI — the committed cache is the data source.
- Config drives behavior. If a change requires editing engine code to support a
  new league type, it's the wrong change.
- Manual override maps are a feature, not a smell. Prefer a small hand-edited
  file over clever inference for the long tail.
