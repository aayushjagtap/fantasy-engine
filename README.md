# Fantasy Basketball Value Engine

A league-adaptive fantasy basketball valuation engine. Ingest NBA box-score
data, compute category or points value from a `LeagueConfig` object (9-cat,
points, punt builds -- swap the config and the whole ranked board re-sorts),
project it forward with a recency-weighted, age-curved projection baseline,
and validate the projection against what actually happened via a backtest
harness. See [`SCOPE.md`](SCOPE.md) for the full v1 scope and success
criteria, and [`BUILD_PLAN.md`](BUILD_PLAN.md) for what's built vs. what's
next.

This is currently a set of importable, independently-runnable modules
(`ingest/`, `crosswalk/`, `engine/`, `backtest/`), not yet a single CLI --
that's the next milestone (M4 / Phase B in `BUILD_PLAN.md`).

## Install

```
pip install -r requirements.txt
```

Requires Python 3.10+.

## The pull-locally-then-commit-cache workflow (read this first)

`stats.nba.com` sits behind Akamai bot protection and **silently drops
connections from datacenter IPs** (AWS, GCP, Azure). If you run
`ingest/nba_boxscores.py` from a cloud environment -- a GitHub Codespace,
a CI runner, most hosted notebooks -- the pull will hang or come back empty,
not fail with a clean error.

The fix: pull once from a normal home/local internet connection, then commit
the cached JSON so every other environment (including CI) reads it from disk
and never touches the network again.

```
# On a local machine, NOT a cloud VM/Codespace:
python ingest/nba_boxscores.py

# This writes data/cache/leaguedashplayerstats_<season>_PerGame_Regular_Season.json
# for each recent completed season. Commit those files -- data/cache/ is the
# one part of data/ that is NOT gitignored (see .gitignore).
git add data/cache/*.json
git commit -m "Refresh cached box scores"
```

`data/cache/` for the 2021-22 through 2025-26 seasons is already committed to
this repo, so a fresh checkout runs everything below fully offline with no
setup.

If a live pull is attempted anyway and the network call fails, the error
message explains the datacenter-IP block and points back here rather than
surfacing a bare timeout.

**`--offline`**: pass `--offline` to `ingest/nba_boxscores.py` (or
`offline=True` to `get_season_boxscores(...)`) to force cache-only lookups.
If a requested season isn't cached, this fails immediately with a clear
message instead of hanging on a network attempt -- useful in any environment
where you expect the cache to already have what you need.

```
python ingest/nba_boxscores.py --offline
```

## Run commands

Every command below is run from the repo root and works fully offline off
the committed cache.

```
# Ingestion sanity check: pulls (from cache) and prints the last 4 seasons.
python ingest/nba_boxscores.py

# Value engine demo: ranks the most recent completed season, std 9-cat then punt-FT%.
python engine/value.py

# Projection: projects the upcoming season from the last 3 completed seasons,
# ranks it, and shows the biggest age/role-driven risers and fallers.
python engine/projection.py

# Per-category explanation for one player (why are they ranked where they are).
python engine/diagnose.py "Jamal Murray"

# Projection-divergence report: players whose season-to-date production diverges
# from their projected line, with a per-category why. NOT a hot/cold signal --
# on season totals this is projection error, not proven regression (the tool
# says so in its output). Demo runs 2025-26 as a simulated in-season season.
python engine/divergence.py

# Roster-aware layer: builds a Team from a handful of drafted players and prints
# its per-category standing relative to the league, inferred punts, and the
# available players whose marginal value (expected category wins added) is
# highest for that roster.
python engine/team.py

# Backtest: projects a held-out season and checks the projection's ranking
# against what actually happened (Spearman rank correlation vs. a naive
# "repeat last season" baseline).
python backtest/validate.py

# Player-ID crosswalk: name normalization + override map, nba_api vs Sleeper report.
python crosswalk/names.py
```

Every module above also has a `--selftest` flag (e.g. `python engine/value.py
--selftest`) -- these are thin wrappers that run that module's own test file
under pytest, kept as a convenience shortcut for sanity-checking one module
without typing the full `pytest` path. pytest must be installed (it's in
`requirements.txt`) for `--selftest` to work.

## Defining a league as a file

`LeagueConfig` no longer has to be constructed in Python. `leagues/` has five
worked examples to copy and edit -- a standard 9-cat league, two punt builds
(`punt_ft.json`, `punt_ast_fg.json`), an 8-cat roto league, and a points
league (`points_espn.json`) that scores makes/attempts directly (FGM/FGA/
FTM/FTA), not just a flat points-per-stat total.

```python
from engine.league_config import LeagueConfig

cfg = LeagueConfig.load("leagues/punt_ft.json")
```

`load()` also accepts `.yaml`/`.yml` if `pyyaml` is installed (it's optional --
not in `requirements.txt` -- and loading a YAML file without it fails with a
message telling you to `pip install pyyaml`). A config round-trips back out
with `cfg.to_json(path)` or `cfg.save(path)` (format chosen by extension).

The Python constructors (`standard_9cat()`, `punt_ft_9cat()`,
`points_league()`) still exist and are used as test fixtures, but a new
league no longer requires editing engine code -- copy the closest file in
`leagues/` and edit it.

These files demonstrate config *shape*, not recommended strategy --
`punt_ast_fg.json` in particular is a deliberately uncommon combination
(punting two categories plus non-default category weights) chosen to
exercise those knobs, not a build to copy blindly into a real draft.

## Two hand-maintained data files

The projection is a box-score model, so two things it structurally can't know
are patched in by hand. Both live under `data/` (which is otherwise
gitignored -- these two are explicitly un-ignored, like `data/cache/`), both
are optional (a missing file changes nothing), and both are surfaced in the
board so they're never silent.

- **`data/rookies_2026.json`** -- the incoming draft class. `project_players`
  only projects players who appeared in the most recent season, so without
  this the entire rookie class is absent from a 2026-27 board. Entries carry
  a per-game line and merge in flagged `is_rookie` (marked `(R)` on the
  board, an `is_rookie` column in the CSV, excluded from the backtest).
  **Stat keys ship as `null` on purpose**: `load_rookies()` skips any entry
  whose line isn't filled in and logs how many it skipped, so an unfilled
  rookie stays off the board rather than showing up as a fabricated
  projection. `nba_id`s are synthetic negatives until the real ones exist --
  see the file's `_comment` for the lifecycle (purge it once 2026-27 actuals
  are ingested, or players double-count).

- **`data/role_overrides.json`** -- `{nba_id: multiplier}`. The role-trend
  signal reads a minutes trajectory and can't see an offseason team change,
  so it fades players who just landed bigger roles (and vice versa). An
  override *replaces* the computed trend multiplier for that player; the
  model's own value is still shown next to it in `--explain`. Ships empty --
  every entry is a hand judgement about one player.

  ```
  # who changed teams for 2026-27, ranked by projected value -- the shortlist:
  python -m cli.board --role-audit --league leagues/standard_9cat.json
  ```

## Projection-divergence report (`--divergence`)

```
python -m cli.board --divergence --league leagues/standard_9cat.json --csv out/divergence.csv
```

Ranks the projected line and the season-to-date production line for every
player, and flags where the two disagree materially -- reported as rank delta
("projected #24, producing like #4") plus a per-category why reused from
`--explain`'s machinery. Per-game basis; sample size is handled by a
reliability weight (`games / (games + 25)`); only players projected inside the
draft pool are eligible.

**This is a projection-divergence report, not a buy-low/sell-high oracle.** On
season totals, a divergence is projection error -- it cannot separate a streak
that will regress from a projection that was simply wrong about a player's
role or talent. The flagged lists overlap the backtest's "breakouts we missed"
and "busts" by construction. Read a row as "look closer here." A true in-season
signal needs per-game game-log ingest (recent-form vs season-form), which
doesn't exist until the 2026-27 season is underway -- see `BUILD_PLAN.md`.

With no 2026-27 data yet, the demo (`python engine/divergence.py`) runs 2025-26
as a simulated in-season season: projected from its three prior seasons, versus
2025-26 actuals.

## The roster-aware layer (`engine/team.py`)

Every board above ranks players against a static pool. In a category league a
player's value also depends on the roster he is joining -- a third elite
shot-blocker is worth less to a team already winning blocks, and a punt-FT%
team should happily give away a 90% free-throw shooter. `Team` makes that
expressible:

```python
from engine.league_config import LeagueConfig
from engine.team import Team

cfg = LeagueConfig.load("leagues/standard_9cat.json")
team = Team(cfg, projected_players, roster_ids=[1629029, 203999, ...])

team.standing()                 # per-category z + win prob vs a typical league team
team.detect_punts()             # categories effectively conceded (reported, not applied)
team.marginal_value(candidate)  # expected category wins added by one more player
```

- **"Relative to the league"** is the mean and spread across `num_teams`
  synthetic rosters partitioned from the top of the value board (snake by
  default). Snake compresses that spread, so standing z-scores are trustworthy
  in order but overstated in magnitude -- `python engine/team.py` prints the
  `sigma` under snake vs a random partition so the effect is visible.
- **Ratio categories** (FG%, FT%) are aggregated by volume -- total makes over
  total attempts -- never averaged.
- **h2h vs roto**: `LeagueConfig.matchup_format` (`h2h` default, `roto`
  available; `leagues/roto_8cat.json` sets it). Under the current variance-free
  model expected roto points are an affine transform of the h2h category-win
  probability, so the two rank rosters identically; the field selects reporting
  units. They diverge only with a per-category weekly variance, which needs
  game-log ingest (see `BUILD_PLAN.md`).

The draft assistant and trade analyzer are the next milestone and both build on
this object.

## Tests

```
pytest -q
```

The full suite runs offline, using the committed cache -- no network access
required or attempted. This is also what CI runs (`.github/workflows/tests.yml`)
on every push and pull request.
