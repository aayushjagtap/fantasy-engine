"""tests/test_divergence.py -- M5, engine/divergence.py.

Fully offline: synthetic projected/actual line dicts, no ingest / nba_api.
The feature is a projection-divergence report, NOT a hot/cold detector -- these
tests pin the mechanics (direction, sample-size weighting, the draftable gate,
the per-category why), not any claim about future regression.
"""
from __future__ import annotations

import pytest

from engine.divergence import _reason_strings, projection_divergence
from engine.league_config import points_league, standard_9cat


def _mk(name, **stats):
    base = dict(name=name, gp=70, pts=12, reb=5, ast=3, stl=1.0, blk=0.5,
                tov=1.5, fg3m=1.5, fg_pct=0.46, fga=10, ft_pct=0.78, fta=3)
    base.update(stats)
    return base


def _pool(n=40, start=100):
    """A stable filler pool: n players with small deterministic spread so every
    category has non-zero variance, identical between projected and actual so
    they never diverge and the pool baseline barely moves when a real mover is
    swapped in (mirrors a real ~370-player pool, unlike a 6-player toy)."""
    out = {}
    for k in range(n):
        f = k / n
        out[start + k] = _mk(f"fill{k}", pts=8 + 6 * f, reb=3 + 4 * f, ast=2 + 3 * f,
                             stl=0.6 + 0.8 * f, blk=0.3 + 0.7 * f, fg3m=0.8 + 2 * f,
                             tov=1.0 + 1.5 * f, fga=7 + 8 * f, fta=2 + 4 * f,
                             fg_pct=0.44 + 0.06 * f, ft_pct=0.72 + 0.16 * f)
    return out


# id 3 "riser" blows up vs its projection; id 4 "faller" collapses. Everyone
# else (ids 1, 2, and the filler pool) is projected == actual.
_MOVERS_PROJ = {
    1: _mk("elite",  pts=26, reb=8, ast=6, stl=1.5, blk=1.0, fg3m=2.5, fga=18, fta=7),
    2: _mk("steady", pts=18, reb=6, ast=4),
    3: _mk("riser",  pts=10, reb=4, ast=2, stl=0.7, blk=0.3, fg3m=0.8, fga=9,  fta=2),
    4: _mk("faller", pts=20, reb=7, ast=5, stl=1.4, blk=0.8, fg3m=2.0, fga=15, fta=6),
}
_MOVERS_ACT = {
    1: dict(_MOVERS_PROJ[1]),
    2: dict(_MOVERS_PROJ[2]),
    3: _mk("riser",  pts=25, reb=9, ast=6, stl=1.7, blk=1.1, fg3m=3.0, fga=17, fta=6),
    4: _mk("faller", pts=7,  reb=3, ast=2, stl=0.4, blk=0.2, fg3m=0.5, fga=6,  fta=2),
}
PROJECTED = {**_MOVERS_PROJ, **_pool()}
ACTUAL = {**_MOVERS_ACT, **_pool()}
POOL_SIZE = len(PROJECTED)  # lenient draftable gate: everyone is "draftable"


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_over_and_under_flagged_with_correct_direction_and_rank_sign():
    rows = projection_divergence(PROJECTED, ACTUAL, standard_9cat(),
                                 min_gp=1, pool_size=POOL_SIZE, threshold=1.0)
    got = _by_name(rows)
    assert got["riser"]["direction"] == "over"
    assert got["riser"]["rank_delta"] > 0        # producing better than projected
    assert got["riser"]["value_delta"] > 0
    assert got["faller"]["direction"] == "under"
    assert got["faller"]["rank_delta"] < 0
    assert got["faller"]["value_delta"] < 0
    # unchanged players never flag (stable filler pool -> no baseline drift)
    assert "elite" not in got and "steady" not in got
    assert not any(r["name"].startswith("fill") for r in rows)


def test_below_threshold_not_flagged():
    rows = projection_divergence(PROJECTED, ACTUAL, standard_9cat(),
                                 min_gp=1, pool_size=POOL_SIZE, threshold=99.0)
    assert rows == []


def test_reliability_weight_gates_small_samples():
    """Same raw divergence; a full-season sample clears the bar, a 15-game one
    (shrunk by gp/(gp+k)) does not."""
    mover_full = _mk("mover", pts=16, reb=6, ast=3.5, stl=1.1, blk=0.6, fg3m=1.6,
                     fga=12, fta=3.5, gp=70)
    proj = {1: _mk("mover", pts=11, reb=4, ast=2, stl=0.7, blk=0.3, fg3m=0.8,
                   fga=9, fta=2), **_pool()}
    full = {1: mover_full, **_pool()}
    small = {1: dict(mover_full, gp=15), **_pool()}
    psize = len(proj)

    # identical raw divergence; full-season weight ~0.74, 15-game weight ~0.375.
    # A bar between the two shrunk values flags one and not the other.
    thr = 5.0
    assert "mover" in _by_name(projection_divergence(proj, full, standard_9cat(),
                                                     min_gp=1, pool_size=psize, threshold=thr))
    assert "mover" not in _by_name(projection_divergence(proj, small, standard_9cat(),
                                                         min_gp=1, pool_size=psize, threshold=thr))
    # the small-sample row IS present once the bar drops below its shrunk weight
    assert "mover" in _by_name(projection_divergence(proj, small, standard_9cat(),
                                                     min_gp=1, pool_size=psize, threshold=0.3))


def test_min_flag_gp_floor_suppresses_flag_entirely():
    small_actual = dict(ACTUAL)
    small_actual[3] = dict(ACTUAL[3], gp=18)
    rows = projection_divergence(PROJECTED, small_actual, standard_9cat(),
                                 min_gp=1, pool_size=POOL_SIZE, threshold=0.1, min_flag_gp=25)
    assert "riser" not in _by_name(rows)


def test_draftable_gate_excludes_deep_pool_projection():
    """A player projected outside the draft pool is projection churn, not a
    roster call -- excluded no matter how large the divergence."""
    # a mover projected dead last: weaker than every filler line
    proj = {1: _mk("deep", pts=1, reb=1, ast=0.5, stl=0.2, blk=0.1, fg3m=0.1,
                   fga=2, fta=0.5, fg_pct=0.40, ft_pct=0.60), **_pool()}
    act = {1: _mk("deep", pts=22, reb=9, ast=6, stl=1.8, blk=1.2, fg3m=3.0,
                  fga=17, fta=6), **_pool()}
    full = len(proj)
    assert "deep" in _by_name(projection_divergence(proj, act, standard_9cat(),
                                                    min_gp=1, pool_size=full, threshold=0.5))
    assert "deep" not in _by_name(projection_divergence(proj, act, standard_9cat(),
                                                        min_gp=1, pool_size=full - 1, threshold=0.5))


def test_reasons_name_the_moved_category():
    """Only FG% moves (same volume), and it moves up -> FG_PCT is a listed
    driver, phrased current-first with the projection for contrast."""
    proj = {1: _mk("p", fga=12, fg_pct=0.44), **_pool()}
    act = {1: _mk("p", fga=12, fg_pct=0.62), **_pool()}  # +18 pts of FG%, same volume
    rows = projection_divergence(proj, act, standard_9cat(),
                                 min_gp=1, pool_size=len(proj), threshold=0.3)
    row = _by_name(rows)["p"]
    assert row["direction"] == "over"
    assert any(s.startswith("FG_PCT") for s in row["reasons"])
    assert row["cat_deltas"]["fg_pct"] > 0
    joined = " ".join(row["reasons"])
    assert "0.620" in joined and "proj 0.440" in joined


def test_rows_sorted_by_abs_weighted_delta_desc():
    rows = projection_divergence(PROJECTED, ACTUAL, standard_9cat(),
                                 min_gp=1, pool_size=POOL_SIZE, threshold=0.1)
    mags = [abs(r["weighted_delta"]) for r in rows]
    assert mags == sorted(mags, reverse=True)


def test_row_shape():
    rows = projection_divergence(PROJECTED, ACTUAL, standard_9cat(),
                                 min_gp=1, pool_size=POOL_SIZE, threshold=1.0)
    expected = {"nba_id", "name", "position", "projected_rank", "actual_rank",
                "rank_delta", "gp", "value_delta", "weighted_delta", "reliability",
                "direction", "reasons", "cat_deltas"}
    assert set(rows[0]) == expected
    assert set(rows[0]["cat_deltas"]) == {c.value for c in standard_9cat().active_categories}


def test_points_league_rejected_with_clear_message():
    with pytest.raises(ValueError, match="category leagues only"):
        projection_divergence(PROJECTED, ACTUAL, points_league(), min_gp=1)


def test_empty_inputs_return_empty():
    assert projection_divergence({}, {}, standard_9cat()) == []
    assert projection_divergence(PROJECTED, {}, standard_9cat()) == []


def test_reason_strings_counting_vs_ratio_format():
    from engine.league_config import StatCategory as SC

    deltas = {SC.PTS: 1.0, SC.FG_PCT: 0.8}
    proj = _mk("x", pts=15, fga=10, fg_pct=0.45)
    act = _mk("x", pts=20, fga=10, fg_pct=0.52)
    out = _reason_strings(deltas, proj, act, "over", n=2)
    assert out[0] == "PTS 20.0 vs proj 15.0 (+5.0)"
    assert out[1].startswith("FG_PCT 0.520 vs proj 0.450 (+0.070) on 10.0 FGA (proj 10.0)")
