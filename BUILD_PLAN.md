# BUILD_PLAN.md — path from current state to a usable 2026-27 draft tool

Written 2026-08-11. Companion to `SCOPE.md` (which defines *what* v1 is).
This file defines *what to build next and in what order*. 

Updated 2026-10-23. Batch 1 was completed at commit `c95ee54` with 192 tests
passing. Batch 2 was completed at commit `f1ac432` with 196 tests passing.

### Batch 1 — correctness cleanup (complete)

- `--role-audit` now reports model trend and effective override separately.
- `Team.marginal_value(drop_id=...)` compares the original roster with the
  post-swap roster on one reference baseline; add-only behavior is unchanged.
- The flat value benchmark remains intact. A separate positional comparison
  scores VOR ordering against target-season realized value on one shared player
  population. The old `+0.000` comparison used the unchanged `value` field, so
  it is not evidence about positional ranking benefit. Results need to be rerun.
- Rookie and role-override files are target-season scoped; projection entry
  points pass the projection target season and ignore mismatched files.
- Draft legality always enforces total capacity. An overfull or positionally
  unassignable existing roster admits no additions until repaired; missing
  position data skips positional checks only.
- Regression tests passed as part of the 192-test suite at `c95ee54`.

### Batch 2 — safe roster refresh and player coverage audit

The roster CLI now preserves cached behavior by default, supports explicit
`--force` refresh, rejects `--force --offline`, validates fetched team roster
responses before cache replacement, and writes cache files atomically. The new
`python -m cli.roster_audit` report is cache-only and separates upcoming
current-roster membership, fallback membership, and their merged view. It
reports team coverage, position gaps, latest-season stats gaps, older cached
history, and detectable identity conflicts. This batch was committed and its
196-test suite passed at `f1ac432`. It made no projection change.

### Batch 3 — returning veterans and current-roster eligibility

The upcoming projection path requires a complete target-season roster cache
and restricts membership to that snapshot. Current-roster players absent from
the newest stats season can use cached history from the three most recent
completed seasons if their last appearance is no more than two missed seasons
old. Their age advances by the actual season gap; expected games receive a
named 0.75-per-missed-season availability assumption. The returner history is
not treated as a consecutive playing-season minutes trend. Roster presence
does not establish health or playing status. Regression tests are added, with
results pending the user's run.

Historical projections and the backtest continue using their prior population
and no future current-roster membership. Because the repository has no
historically timed roster snapshots, the backtest does not evaluate the new
returner eligibility policy; no comparable score is claimed.

The verified rookie rebuild also remains upcoming. The current
`data/rookies_2026.json` contains synthetic negative IDs and null stat lines;
Batch 3 does not verify rookie identities or create stat projections. Current-
season actual ingestion and automatic daily stats/roster updates are later
batches; they must preserve historical season caches and saved preseason
projection baselines.

---

## Current state

Historical snapshot from 2026-08-26: `pytest -q` had 165 passing tests at that
point. Batch 1's later 192-test result is recorded above.

| Milestone | Status |
|---|---|
| M0 crosswalk spike | Done. Now `crosswalk/names.py` (renamed from `spike.py`) with `overrides.json` + `resolve()` — C5. |
| M0.5 DARKO decision | Done (`DARKO_NOTES.md`). DARKO = benchmark, not dependency. |
| M1 ingest | Done (`ingest/nba_boxscores.py`). Season lines, not game logs. |
| M2 config | Done. Constructors kept as fixtures; leagues are files now — B1. |
| M3 value engine | Done (`engine/value.py`) + `engine/diagnose.py`. `SCALE_FIELDS` bug fixed — A3. |
| Projection | Done (`engine/projection.py`). Recency-weighted, age-curved, role-trend. |
| M4 draft board CLI | Done (`cli/board.py`) — B2. Dual-lens board, `--explain`, `--compare`, `--role-audit`, `--divergence`, CSV. |
| M5 buy-low / sell-high | **Partial — shipped as a projection-divergence report** (`engine/divergence.py`, `cli/board.py --divergence`). True in-season hot/cold is blocked on game-log ingest; see "M5" under Phase D. |
| M6 backtest | Done (`backtest/validate.py`). Single-population fix + role-trend ablation — A4. |

| Phase B/C item | Status |
|---|---|
| A1 packaging + README | Done. `requirements.txt`, `README.md`, `.github/workflows/tests.yml`. |
| A2 real test suite | Done. `tests/` under pytest; `--selftest` flags kept as thin wrappers. |
| A3 `SCALE_FIELDS` incomplete | Done. `fgm`/`ftm`/`min` added + regression test. |
| A4 backtest mismatched populations | Done. `_score_predictors` computes one intersection, prints N. |
| A5 `basis="total"` misnomer | Done. Renamed **availability-adjusted**; `"total"` kept as an alias. |
| B1 config as data | Done. `LeagueConfig.load/save`, `leagues/*.json` (5 examples). |
| B2 the CLI | Done. `python -m cli.board`. |
| C1 ingest positions/teams | Done (`ingest/nba_rosters.py`, `CommonTeamRoster`, per-team disk cache). |
| C2 position-aware replacement | Done. `REPLACEMENT_MODES` = flat / hybrid / strict; **flat stays default** (see Decisions). |
| C3 rookies | Loader/merge done; verified rebuild upcoming. Current 17 entries use synthetic negative IDs and null stats, so none reach the board. |
| C4 role overrides | Override application and explanation done; numeric decisions open. Current file has no numeric entries. `_season` scopes the file; verify each judgment and rationale against refreshed roster data before adding. |
| C5 crosswalk override map | Done. `crosswalk/overrides.json` (ships empty) + `load_overrides()`/`resolve()`; `names.py` no longer a spike. |

| Phase D item | Status |
|---|---|
| E1 `engine/team.py` — roster state | Done (2026-08-26). `Team` object: per-category `category_totals` (ratio cats volume-weighted), `standing` (z vs a synthetic-team baseline drawn from the draft pool, size-prorated for mid-draft use), `expected_category_wins` / `marginal_value` (the draft-assistant primitive — exact recompute, diminishing returns fall out of the win-prob sigmoid), `detect_punts` (inferred, never written back to config), `player_breakdown` (reuses `diagnose.category_contributions`). New `LeagueConfig.matchup_format` (`h2h` default / `roto`). **Revised 2026-08-26** (E2 prep — see Decision f revision): `standing()` no longer clamps `z`; proration is true first-`k`-picks; `marginal_value` delta documented as ranking-only. |
| E2 `engine/draft.py` — draft assistant (engine half) | Done (2026-08-26). `recommend(team, players, available_ids)` ranks candidates by a blend of `marginal_value` and raw VOR: `w_mv = clamp(k / starter_slots, 0, 1)`. `legal_additions` uses full bipartite matching (G/F/C only); missing positions skip positional matching only after capacity is enforced. Invalid existing rosters admit no additions until repaired. Positional pressure is reported but not applied; settings remain unvalidated pending a draft sim. VOR uses flat replacement. |

Still deferred: **`cli/draft.py`** (the front door for E2 — next session), the
trade analyzer (also consumes `engine/team.py`), the rest of M5 (game-log
ingest — see Phase D), the Sleeper/ADP stretch.

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
`_next_season_label("2025-26")` → `"2026-27"`. Pinned against drift by
`test_recent_completed_seasons_pinned_to_2026_08_11` (A2).

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

### C5. Ship the crosswalk override map — DONE (2026-08-25)
`crosswalk/spike.py` → `crosswalk/names.py` (git-renamed; the "spike" framing is
gone). `crosswalk/overrides.json` ships with an empty `overrides: {}` and a
`_comment` documenting its two jobs: cross-source spelling gaps, and the
suffix-collision case (`normalize_name` strips Jr./Sr./II–V, so two distinct
active players differing only by a suffix collapse onto one key). `load_overrides()`
+ `resolve(name, key_map, overrides)` return `override` / `ok` / `collision` /
`miss`; `run()` now scans the whole active pool for colliding keys and prints
them loudly (empty as of the last run — no override entries needed yet).
`normalize_name()` stays pure; overrides are a `resolve()`-layer concern.

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

### M5 — buy-low / sell-high  (partial: 2026-08-25)

**Shipped this pass — a projection-divergence report.** `engine/divergence.py`
(`projection_divergence()`) + `cli/board.py --divergence` + CSV. For every
player it ranks the projected line and the current-production line within one
common population, reports the gap in units a user reads directly ("projected
#24, producing like #4"), and reuses `engine/diagnose.py`'s per-category
machinery — factored out into `category_contributions()` — to say *why*
("FG_PCT 0.561 vs proj 0.513 (+0.048) on 18.2 FGA"). Comparison is on the
per-game basis (rate, not accumulation); sample size is handled separately by
a reliability weight `gp / (gp + 25)` applied before the flag threshold; only
players projected inside the draft pool are eligible (a deep-pool divergence
is projection churn, not a roster call). Tested against 2025-26 as a simulated
in-season season: project 2025-26 from its three prior seasons (as the
backtest does), treat 2025-26 actuals as "current production."

**What it is NOT, and why the rest of M5 is deferred.** A season-total
divergence is *projection error*. It cannot separate "producing above true
talent, will regress" (a real sell-high) from "the projection was simply
wrong about role/health/talent" (nothing to regress to). The two are the same
number here. By construction the flagged players overlap heavily with the
backtest's "breakouts we missed" / "busts" lists (`backtest/validate.py`) —
those are the projection's known blind spots, surfaced from the other side.
The report says this in its docstring and in the CLI output header; it is
framed as "look closer here," not as a trade signal.

**What true in-season M5 needs — a real ingest addition, once the season is
underway:** per-game game logs, so a player's *recent-N-games* form can be
measured against their *season-to-date* form. That is the only way to tell a
shooting hot streak (will regress) from a sustained role change (won't).
Concretely:
- `nba_api` `PlayerGameLog` (per-player, per-game rows), or
- `LeagueDashPlayerStats` with `DateFrom` / `DateTo` — two whole-league pulls
  (last ~15 days, and season-to-date) differenced.
Same pull-locally-then-commit-cache workflow as `ingest/nba_boxscores.py`
(datacenter-IP block still applies). Then `divergence` compares recent-form
value vs season-form value instead of actual-vs-projected, and the reliability
weight keys off games-in-window. No data for this exists until after Oct 20,
2026 (2026-27 opening night).

**SCOPE.md gate 3 directional check — not answerable with the current cache.**
"Share of sell-high flags whose rest-of-season production fell toward their
projection" needs a season split into "through game N" / "games N+1..82".
`data/cache/` holds season totals only, so this check cannot be run — and was
not faked. It unblocks with the same game-log ingest above. A weaker
season-to-season reversion proxy (does a season-X divergence shrink in season
X+1) is computable from the cache but was deliberately not shipped as
"validation": it still can't isolate regression from projection-miss, so
presenting it as a gate-3 result would overclaim.

### E1. `engine/team.py` — roster state  (DONE 2026-08-26)

`Team(config, players, roster_ids)` for a category league. Shipped:
- `category_totals` — counting cats sum; ratio cats (FG%/FT%) aggregated as
  `sum(makes) / sum(attempts)`, never averaged (same volume trap
  `_pool_ratio_pct` solves for the pool).
- `league_baseline` / `standing` — "relative to the league" is defined as the
  mean and spread across `num_teams` synthetic rosters partitioned from the
  top `num_teams * roster_slots` of the value board (`baseline_method="snake"`
  default, `"random"` fixed-seed available). Counting-cat baseline is prorated
  to the synthetic teams' first-`k` picks so `standing`/`detect_punts` mean
  something mid-draft, not only at a full roster. `win_prob = Phi(z / sqrt(2))`;
  `z` is **not** capped (see Decision f revision — Phi already bounds it).
- `expected_category_wins` / `marginal_value` — the objective and the
  draft-assistant primitive. Exact recompute against a fixed baseline;
  diminishing returns are emergent from the sigmoid (a third elite blocker on
  a blocks-strong roster moves `P(win blk)` far less than on an average one —
  locked by `test_third_elite_blocker_adds_less_than_first` and
  `test_ft_shooter_worth_less_to_a_punt_ft_roster`).
- `detect_punts` — categories with `z <= -1.5`, reported as data; the config
  is never mutated.
- `player_breakdown` — per rostered player, `diagnose.category_contributions`
  reused verbatim (no re-derived z math).

Design answers recorded under Decisions (e, f). The draft-assistant engine
(`engine/draft.py`) is now built on top of this (E2, 2026-08-26); its CLI and
the trade analyzer are still to come. Original E1 notes:
- **Draft assistant:** given my roster + available players, who most improves my
  weakest *contested* categories? (Marginal value, not raw VOR.) — done as
  `engine/draft.recommend`; CLI pending.
- **Trade analyzer:** a trade cannot be evaluated by summing VOR in a category
  league. A third elite shot-blocker is worth less to a team already winning
  blocks; a punt-FT% team should happily give away a 90% free-throw shooter.
  Evaluate by change in expected category wins for **both** sides.
- **Punt detection:** infer which categories a roster has effectively already
  punted, and re-rank accordingly.

---

## Decisions (recorded)

Everything here is settled and reflected in the code. Numbers are from the
committed offline cache; reproduce with `python backtest/validate.py` and the
sweep scripts noted.

### a. History window: 3 seasons, `AVAIL_ALPHA = 0.5`
Swept history length 3 / 4 / 5 × `avail_alpha` 0.3 / 0.5 / 0.7 against 2025-26
(n = 370). Spearman is **monotonic in both knobs**: fewer seasons is better at
*every* alpha. Going 3 → 5 seasons costs −0.0135 Spearman. Kept `RECENCY_WEIGHTS`
at 3 entries and `AVAIL_ALPHA = 0.5`. (See the post-C1 idea at the end of this
file: the 3-season limit is right for the box-score *average* but arbitrary for
slow-moving durability/role traits — decoupling those windows is a later item,
not a change to this decision.)

### b. `Z_CAP` stays 3.0
Swept the cap from 2.5 through uncapped. Across that whole range only one player
moves materially: Giannis Antetokounmpo, #50 → #94 as the cap loosens, because
uncapped his FT% drag outweighs his FG% credit. Everyone else is stable. No
reason to move off 3.0.

### c. Flat replacement stays the default
The previously reported `+0.000` comparison evaluated the unchanged `value`
field, while positional replacement changes VOR and rank. It therefore says
nothing about positional ranking benefit. Flat remains the default pending the
new VOR-order comparison. The old cache-based center/guard counts below are
historical diagnostics only. The prior snapshot had ~113
C-eligible vs ~301 G-eligible players (verified against the roster cache:
C = 113, G = 301) competing for ~12 C vs ~36 G starter slots in a 12-team
league, so the replacement *percentile* lands at a comparable depth for both —
hybrid removes almost none of the center scarcity it was meant to capture while
adding a knob.
`flat` remains the default and the number the recorded backtest is measured on;
`hybrid` / `strict` stay available via `--replacement`.

### d. Post-A4 backtest baseline (2025-26 holdout, n = 358)
`python backtest/validate.py`:

| predictor | Spearman |
|---|---|
| naive persistence (repeat last season) | 0.679 |
| ours, role-trend OFF | 0.676 |
| ours, role-trend ON | 0.689 |

Role-trend adds +0.013 over OFF, +0.010 over naive. Top-30 hit rate: 15/30 of
our projected top 30 finished top 30. `hybrid` reproduces all three to ±0.000.

**The pre-A4 commit messages cite older, non-comparable numbers.** `4e09194`
("0.679 → 0.687") and `4d41e1a` ("adds +0.011 / +0.017") were measured before
`_score_predictors` computed a single common population — each predictor was
scored on its own intersection with actuals and then differenced, so those
deltas are apples-to-oranges. Trust the table above, not those messages.

### e. E1 objective: one function, `units="category_wins" | "roto_points"`

`LeagueConfig.matchup_format` added (`h2h` default — the common case and where
roster-aware value diverges most from static VOR; `roto` also supported).
Under `engine/team.py`'s variance-free model the two are **not** different
optima: expected roto points in category *c* is
`1 + (num_teams-1) * Phi(z_c / sqrt(2))`, an affine transform of the H2H
category-win probability `Phi(z_c / sqrt(2))` with positive slope and a
constant that cancels in any marginal difference — so both units rank rosters
identically. `units` therefore selects reporting units, not the math. They
separate only once a per-category weekly variance `tau_c` is estimable from
game logs (h2h then compresses high-variance cats — blk, stl, fg3m,
thin-volume FT% — toward a coin flip; roto barely feels it). Not built:
`tau_c` needs game-log ingest that doesn't exist until 2026-27 is underway.

### f. E1 league baseline: snake partition, size-prorated, availability-adjusted

- **Baseline = synthetic rosters from the pool.** `num_teams` teams snake-dealt
  from the value-sorted top `num_teams * roster_slots`; `(mean, std)` per
  category across them is the league reference. `baseline_method="random"`
  (fixed seed `20260826`) is available. Snake gives every synthetic team a
  near-identical tier mix, so it **understates `sigma_c`** vs a real league of
  punt builds → **inflates every `|z|`**. `random` is a looser lower bound;
  `_demo()` prints both. Standing z is ordinally trustworthy, its magnitude an
  overestimate. Documented in the module.
- **Size proration.** Counting-cat baseline scaled by
  `roster_size / roster_slots` so a mid-draft roster is compared to a typical
  team's *first k picks*' worth (linearly approximated), not a full team.
  Ratio cats unscaled. `marginal_value` pins both endpoints to the post-move
  size so its delta is the player, not a moving yardstick.
- **`basis` defaults to `availability_adjusted`**, matching `compute_values`, so
  board rank and marginal value are read on one basis. `per_game` overridable.

**Revised 2026-08-26 (E2 prep — four corrections to E1):**
1. *Snake bias is category-split, not blanket.* On the committed pool snake
   `sigma_c` runs *above* random for `stl/blk/fg3m/fg_pct`; it compresses only
   the value-correlated cats (`pts/reb/ast/tov/ft_pct`). Docstrings reworded.
2. *Proration is now true first-k, not linear.* `standing()` compares a
   `k`-roster to the synthetic teams' actual first `k` picks
   (`Team._prorated_baseline(k)`), falling back to the full-team baseline once
   `k` reaches the synthetic team size. The old `k / roster_slots` scaling
   overstated every partial roster (a five-big roster read `pts z = +1.25`;
   first-k puts it negative).
3. *`Z_CAP` removed from `standing()`* (`z_cap` now defaults to `None`). Phi
   already bounds the objective in `[0,1]`, so the cap only destroyed gradient
   — under it ~5–6 of 9 categories pinned at `±3` and `marginal_value`
   returned *exactly* 0 in each, deciding picks on the survivors. Uncapped `z`
   of `-5` → `P(win) ~ 0.0002` is honest over-confidence from the compressed
   `sigma_c`; the real fix is `tau_c`. The draftable-pool selection and
   `player_breakdown()` keep `Z_CAP` (summed-z board contexts).
4. *`marginal_value`'s delta* is documented as valid only for ranking at a
   fixed roster size — not comparable across sizes, not an absolute
   category-win count (pinning both endpoints to the post-move size
   under-credits "before").

## Still open

1. **Sleeper/ADP** for Success Gate #3. `SCOPE.md` names preseason ADP as the
   baseline; `validate.py` uses naive persistence. Either pull ADP or formally
   amend the gate — see `SCOPE.md`'s gate-3 status line. Unresolved.
2. **Backtest holdout**: defaults to the latest completed season (2025-26).
   Fine for now; revisit once 2026-27 actuals exist.
3. **Route decision**: resolved E-first. M5's ranked-list half (the
   projection-divergence report) shipped, then `engine/team.py` (E1) and the
   draft-assistant engine `engine/draft.py` (E2 engine half), both 2026-08-26.
   Next: `cli/draft.py`, then the trade analyzer.
5. **Draft-assistant unvalidated constants**: the blend curve `w_mv = k /
   starter_slots`, `scarcity_lambda` (default 0.0 — off), and the scarcity
   relevance window are all reasoned, not fit. A draft simulation would let
   them be swept; until then scarcity is reported but not applied.
4. **Game-log ingest for real M5 + gate-3 directional check.** Both are blocked
   on per-game data that doesn't exist until 2026-27 opening night (Oct 20,
   2026). See "M5" under Phase D for the exact endpoints. Not startable now.

---


Idea (post-C1): decouple signal windows from the averaging window. The 3-season limit is right for the box-score average (proven by sweep) but arbitrary for slow-moving traits. Let expected_gp read all 5 cached seasons for a better durability estimate, and consider usage% (needs an advanced-measure ingest call) as a role signal over a longer window.
