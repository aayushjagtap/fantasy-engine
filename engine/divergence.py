"""
engine/divergence.py -- M5, the projection-divergence report.

WHAT THIS IS. For every player, this compares their season-to-date production
against the line the projection expected of them, and surfaces where the two
disagree by a material amount. Output is in units a user reads directly:
"projected #24, producing like #4" (rank delta), plus a per-category breakdown
of which stats moved (reused from engine/diagnose.py).

WHAT THIS IS NOT -- read before trusting a row as a trade signal.
A true buy-low / sell-high call is a claim about the FUTURE: "this player is
producing above their true talent and will regress toward it" (sell high), or
below and will recover (buy low). This module cannot make that claim, because a
season-total divergence cannot tell the two explanations apart:

  * a genuine hot / cold streak that will regress, versus
  * a projection that was simply wrong about the player's role, health, or
    talent -- in which case current production IS the new true level and there
    is nothing to regress to.

Both look identical here: a big gap between projected and actual season value.
By construction, the players this flags overlap heavily with the backtest's
"breakouts we missed" and "busts" lists (backtest/validate.py) -- those are the
projection's known blind spots, surfaced from the other side.

Separating streak from projection-miss needs IN-SEASON GAME LOGS: a player's
last-N-games form measured against their season-to-date form. That is a real
ingest addition (nba_api PlayerGameLog, or LeagueDashPlayerStats with
DateFrom/DateTo), pulled on a local machine and cached, and it does not exist
until the 2026-27 season is underway. See BUILD_PLAN.md -> "M5" for the plan.

Until then: read a row as "the projection and current production disagree here,
go look at why," not as an automatic sell or buy.

TESTING IT NOW. There is no 2026-27 data yet, so the demo runs against 2025-26
as a simulated in-season season: project 2025-26 from its three prior seasons
(exactly as the backtest does), then treat 2025-26 actuals as "current
production." Every simulated player has a full season of games, so the
sample-size reliability weight below is ~uniform in the demo; it only bites in
real in-season use, when "current production" is 15-40 games.

Run from the repo root:
    python engine/divergence.py            # 2025-26 simulated in-season divergence report
    python engine/divergence.py --selftest # offline checks, no network
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.diagnose import category_contributions
from engine.league_config import CATEGORY_META, ScoringType
from engine.value import _apply_basis, _draft_pool_ids, compute_values

# Single-sourced so the CLI prints exactly the caveat the module docstring makes.
CAVEAT = (
    "NOT a hot/cold signal: a season-total divergence cannot tell a streak that "
    "will regress from a projection that was simply wrong. Read a row as 'look "
    "closer here', not as an automatic buy/sell. True in-season buy-low/sell-high "
    "needs game-log ingest (last-N-games vs season-to-date) -- see BUILD_PLAN.md."
)

# Reliability shrink: a divergence measured over `gp` games is scaled by
# gp / (gp + RELIABILITY_K) before being tested against `threshold`. K = 25 puts
# the half-trust point at a quarter-season -- a 25-game divergence must be ~2x a
# full-season one to clear the same bar. (Chosen, not fit: there is no in-season
# data to fit it on yet. Revisit once game logs exist.)
RELIABILITY_K = 25

# Default material-divergence bar, in reliability-weighted category-value units
# (the same z-sum scale the board's `value` column is in). 2.0 lands ~the top
# decile of |divergence| on the 2025-26 sim for a standard 9-cat league.
DEFAULT_THRESHOLD = 2.0


def _dense_rank(ids, value_by_id):
    """1-based contiguous rank within `ids`, best (highest value) = 1."""
    order = sorted(ids, key=lambda i: value_by_id[i], reverse=True)
    return {i: n for n, i in enumerate(order, 1)}


def _reason_strings(cat_deltas, projected_line, actual_line, direction, n=3):
    """Phrase the top |z-delta| categories that DROVE the flag, using raw
    per-game stats (current value first, projection for contrast):
    'FG_PCT 0.561 vs proj 0.513 (+0.048) on 18.2 FGA (proj 17.9)'."""
    want = 1.0 if direction == "over" else -1.0
    drivers = sorted(
        (c for c, dz in cat_deltas.items() if dz * want > 0),
        key=lambda c: abs(cat_deltas[c]), reverse=True,
    )[:n]
    out = []
    for cat in drivers:
        meta = CATEGORY_META[cat]
        key = cat.value
        pv = projected_line.get(key) or 0.0
        av = actual_line.get(key) or 0.0
        if meta.is_ratio:
            vol = meta.volume_stat
            pvol = projected_line.get(vol) or 0.0
            avol = actual_line.get(vol) or 0.0
            out.append(f"{key.upper()} {av:.3f} vs proj {pv:.3f} ({av - pv:+.3f}) "
                       f"on {avol:.1f} {vol.upper()} (proj {pvol:.1f})")
        else:
            out.append(f"{key.upper()} {av:.1f} vs proj {pv:.1f} ({av - pv:+.1f})")
    return out


def projection_divergence(projected: dict, actual: dict, config, *, min_gp: int = 20,
                          basis: str = "per_game", reliability_k: int = RELIABILITY_K,
                          threshold: float = DEFAULT_THRESHOLD, pool_size: int | None = None,
                          min_flag_gp: int | None = None) -> list[dict]:
    """Players whose `actual` (season-to-date) production diverges materially from
    their `projected` line. One row per flagged player, sorted by
    |weighted_delta| descending.

    NOT a hot/cold detector -- see the module docstring and CAVEAT.

    basis: 'per_game' by default -- divergence is a RATE-of-production signal;
        games played is handled separately by the reliability weight, not by
        letting availability swing the value. (Pass 'availability_adjusted' to
        fold availability in, e.g. for an end-of-season retrospective.)
    reliability_k: games-shrink constant; weighted_delta = value_delta *
        gp / (gp + reliability_k).
    threshold: minimum |weighted_delta| to flag.
    pool_size: only players PROJECTED inside this many board spots are eligible
        to flag -- a divergence in the deep end is projection churn, not a
        roster decision. Defaults to the league's full draft pool
        (num_teams * roster slots).
    min_flag_gp: hard games floor to flag at all (default: min_gp, i.e. no extra
        floor beyond the board's own eligibility cut -- the reliability weight
        does the small-sample work).

    Row fields:
        nba_id, name, position,
        projected_rank, actual_rank,          -- dense ranks within the common set
        rank_delta,                           -- projected_rank - actual_rank
                                                (+ = producing better than projected)
        gp,                                   -- games behind `actual`
        value_delta,                          -- actual value - projected value,
                                                raw category-value units (pre-replacement)
        weighted_delta, reliability,
        direction,                            -- 'over' | 'under'
        reasons,                              -- list[str], top category swings phrased raw
        cat_deltas,                           -- {stat_key: z_delta} for every active category
    """
    if config.scoring_type is ScoringType.POINTS:
        raise ValueError(
            "projection_divergence supports category leagues only -- the "
            "per-category 'why' has no analogue in a points league. "
            "(A points-total divergence would be a straightforward later add.)"
        )
    floor = min_gp if min_flag_gp is None else min_flag_gp

    proj_board = compute_values(projected, config, min_gp=min_gp, basis=basis)
    act_board = compute_values(actual, config, min_gp=min_gp, basis=basis)
    if not proj_board or not act_board:
        return []

    if pool_size is None:
        total_roster = sum(getattr(s, "count", 0) for s in config.roster) or 13
        pool_size = config.num_teams * total_roster

    proj_val = {r["nba_id"]: r["value"] for r in proj_board}
    act_val = {r["nba_id"]: r["value"] for r in act_board}
    common = [i for i in proj_val if i in act_val]
    if not common:
        return []
    proj_rank = _dense_rank(common, proj_val)
    act_rank = _dense_rank(common, act_val)

    # One fixed baseline pool (the actual season's draft pool) for the
    # per-category diff, so a category's z-delta reflects only the player's line
    # moving -- not a season-to-season shift in the pool it's scored against.
    eligible_actual = {i: p for i, p in actual.items() if (p.get("gp") or 0) >= min_gp}
    scaled_actual = _apply_basis(eligible_actual, basis)
    base_pool_ids = _draft_pool_ids(
        scaled_actual, config, min(pool_size, len(eligible_actual)))

    rows = []
    for tid in common:
        if proj_rank[tid] > pool_size:
            continue
        gp = actual[tid].get("gp") or 0
        if gp < floor:
            continue
        value_delta = act_val[tid] - proj_val[tid]
        reliability = gp / (gp + reliability_k) if gp else 0.0
        weighted = reliability * value_delta
        if abs(weighted) < threshold:
            continue
        direction = "over" if value_delta > 0 else "under"

        c_act = category_contributions(actual, config, tid, min_gp=min_gp,
                                       basis=basis, pool_ids=base_pool_ids)
        c_proj = category_contributions({**actual, tid: projected[tid]}, config, tid,
                                        min_gp=min_gp, basis=basis, pool_ids=base_pool_ids)
        if c_act and c_proj:
            cat_deltas = {cat: c_act[cat]["z"] - c_proj[cat]["z"] for cat in c_act}
        else:
            cat_deltas = {}
        reasons = _reason_strings(cat_deltas, projected[tid], actual[tid], direction)

        rows.append({
            "nba_id": tid,
            "name": actual[tid].get("name"),
            "position": "/".join(actual[tid].get("position") or ()) or "?",
            "projected_rank": proj_rank[tid],
            "actual_rank": act_rank[tid],
            "rank_delta": proj_rank[tid] - act_rank[tid],
            "gp": round(gp),
            "value_delta": round(value_delta, 2),
            "weighted_delta": round(weighted, 2),
            "reliability": round(reliability, 2),
            "direction": direction,
            "reasons": reasons,
            "cat_deltas": {cat.value: round(dz, 2) for cat, dz in cat_deltas.items()},
        })

    rows.sort(key=lambda r: abs(r["weighted_delta"]), reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Demo: 2025-26 as a simulated in-season season
# --------------------------------------------------------------------------- #

def _demo() -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from ingest.nba_rosters import attach_positions, load_rosters_or_warn
    from engine.projection import project_players
    from engine.league_config import standard_9cat
    from util.console import configure_stdout_utf8

    configure_stdout_utf8()
    target = recent_completed_seasons(1)[0]
    # the three seasons before `target`, newest first (same window the backtest uses)
    y = int(target.split("-")[0])
    priors = [f"{yr}-{str(yr + 1)[2:]}" for yr in range(y - 1, y - 4, -1)]
    cfg = standard_9cat()

    rosters = load_rosters_or_warn()
    actual = attach_positions(get_season_boxscores(target)[0], rosters=rosters)
    prior_lines = [get_season_boxscores(s)[0] for s in priors]
    prior_lines[0] = attach_positions(prior_lines[0], rosters=rosters)
    # No rookies= / role_overrides=: a historical target season predates both.
    projected, _ = project_players(prior_lines)

    rows = projection_divergence(projected, actual, cfg)
    over = [r for r in rows if r["direction"] == "over"]
    under = [r for r in rows if r["direction"] == "under"]

    print(f"\nProjection-divergence report -- simulated in-season: projected {target} "
          f"from {', '.join(priors)}, vs {target} actuals")
    print(f"basis=per_game  reliability_k={RELIABILITY_K}  threshold={DEFAULT_THRESHOLD}  "
          f"draft pool={cfg.num_teams * sum(s.count for s in cfg.roster)}")
    print(f"\n!! {CAVEAT}\n")

    def _section(title, rs):
        print(f"{title}  ({len(rs)})")
        print(f"{'player':<26} {'pos':>4} {'proj#':>6} {'prod#':>6} {'d':>5} {'gp':>3}  why")
        print("-" * 100)
        for r in rs:
            print(f"{(r['name'] or '?'):<26} {r['position']:>4} {r['projected_rank']:>6} "
                  f"{r['actual_rank']:>6} {r['rank_delta']:>+5} {r['gp']:>3}  "
                  f"{' | '.join(r['reasons'])}")
        print()

    _section("PRODUCING ABOVE PROJECTION  (sell-high candidates IF this is a streak, not a miss)", over)
    _section("PRODUCING BELOW PROJECTION  (buy-low candidates IF this is a slump, not a miss)", under)


def _selftest() -> None:
    """Thin wrapper: runs tests/test_divergence.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_divergence.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("divergence selftest ok: see tests/test_divergence.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
