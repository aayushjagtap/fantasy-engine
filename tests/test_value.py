"""tests/test_value.py -- moved from engine/value.py's --selftest, plus new
regression coverage for the SCALE_FIELDS bug (A3) and the basis rename (A5).
"""
from __future__ import annotations

import pytest

from engine.league_config import (
    CategorySetting,
    LeagueConfig,
    RosterSlot,
    ScoringType,
    StatCategory,
    points_league,
    punt_ft_9cat,
    standard_9cat,
)
from engine.value import (
    SCALE_FIELDS,
    _group_replacement_levels,
    _position_demand,
    compute_values,
)
from tests.fixtures import IRONMAN_FRAGILE_FIXTURE, SIX_PLAYER_FIXTURE, rank_of


def test_z_cap_bounds_outliers():
    capped = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6, z_cap=3)
    loose = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6, z_cap=100)
    assert rank_of(capped, "giannis_like") <= rank_of(loose, "giannis_like")


def test_total_basis_rewards_availability_over_per_game():
    two = IRONMAN_FRAGILE_FIXTURE
    tot = compute_values(two, standard_9cat(), min_gp=1, pool_size=2, basis="availability_adjusted")
    pg = compute_values(two, standard_9cat(), min_gp=1, pool_size=2, basis="per_game")
    assert rank_of(tot, "ironman") < rank_of(tot, "fragile")
    assert pg[0]["value"] == pg[1]["value"]


def test_availability_adjusted_alias_total_matches_new_name():
    two = IRONMAN_FRAGILE_FIXTURE
    new_name = compute_values(two, standard_9cat(), min_gp=1, pool_size=2, basis="availability_adjusted")
    alias = compute_values(two, standard_9cat(), min_gp=1, pool_size=2, basis="total")
    assert new_name == alias


def test_points_league_rewards_availability():
    two = IRONMAN_FRAGILE_FIXTURE
    pl = points_league()
    ptot = compute_values(two, pl, min_gp=1, pool_size=2, basis="availability_adjusted")
    assert rank_of(ptot, "ironman") < rank_of(ptot, "fragile")


def test_punt_ft_promotes_low_ft_big():
    std = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6, basis="per_game")
    punt = compute_values(SIX_PLAYER_FIXTURE, punt_ft_9cat(), min_gp=1, pool_size=6, basis="per_game")
    assert rank_of(punt, "giannis_like") < rank_of(std, "giannis_like")


# --- A3: SCALE_FIELDS was missing fgm/ftm/min, so a points league scoring
# FGM/FGA/FTM/FTA directly didn't scale FGM/FTM credit by availability while
# it did scale FGA/FTA penalty -- quietly wrong for a whole class of league. ---

def test_scale_fields_includes_fgm_ftm_min():
    assert "fgm" in SCALE_FIELDS
    assert "ftm" in SCALE_FIELDS
    assert "min" in SCALE_FIELDS


# --- C2: position-aware replacement level ---

def _pts_only_cfg(roster, num_teams=2):
    return LeagueConfig(
        scoring_type=ScoringType.CATEGORY, num_teams=num_teams,
        categories={StatCategory.PTS: CategorySetting()}, roster=roster,
    )


def _pts_line(name, pts, position):
    return dict(name=name, gp=70, pts=pts, reb=0, ast=0, stl=0, blk=0, tov=0,
                fg3m=0, fg_pct=0.5, fga=1, ft_pct=0.5, fta=1, position=position)


def test_position_demand_hybrid_leaves_util_bench_on_flat_pool():
    cfg = _pts_only_cfg([RosterSlot(position="PG", count=1), RosterSlot(position="C", count=1),
                         RosterSlot(position="UTIL", count=2), RosterSlot(position="BENCH", count=3)])
    demand, flex = _position_demand(cfg, "hybrid")
    assert demand == {"G": 2, "F": 0, "C": 2}   # (PG count 1) * num_teams 2, C likewise
    assert flex == 10                            # (UTIL 2 + BENCH 3) * num_teams 2


def test_position_demand_strict_distributes_flex_across_dedicated_groups():
    cfg = _pts_only_cfg([RosterSlot(position="PG", count=1), RosterSlot(position="C", count=1),
                         RosterSlot(position="UTIL", count=2)])
    demand, flex = _position_demand(cfg, "strict")
    assert flex == 0
    assert demand == {"G": 4, "F": 0, "C": 4}   # UTIL's 4-player demand split 50/50 across G/C


def test_group_replacement_levels_uses_nth_best_eligible_and_omits_empty_groups():
    value = {1: 10, 2: 8, 3: 6, 4: 4, 5: 2}
    positions = {1: ("C",), 2: ("C",), 3: ("C",), 4: ("G",), 5: ("G",)}
    ranked = sorted(value, key=value.get, reverse=True)
    levels = _group_replacement_levels(value, positions, ranked, {"C": 2, "G": 1, "F": 3})
    assert levels["C"] == 8    # 2nd-best center (id 2)
    assert levels["G"] == 4    # 1st-best guard (id 4)
    assert "F" not in levels   # no F-eligible candidates -> caller falls back to flat


def test_hybrid_replacement_rewards_scarce_position_over_flat():
    # 3 centers (shallow: 20/15/5) vs 5 guards (deep: 20/18/16/14/12), 1 slot each + 1 UTIL.
    players = {
        1: _pts_line("c1", 20, ("C",)), 2: _pts_line("c2", 15, ("C",)), 3: _pts_line("c3", 5, ("C",)),
        4: _pts_line("g1", 20, ("G",)), 5: _pts_line("g2", 18, ("G",)), 6: _pts_line("g3", 16, ("G",)),
        7: _pts_line("g4", 14, ("G",)), 8: _pts_line("g5", 12, ("G",)),
    }
    cfg = _pts_only_cfg([RosterSlot(position="C", count=1), RosterSlot(position="G", count=1),
                         RosterSlot(position="UTIL", count=1)])

    flat = {r["name"]: r["vor"] for r in
            compute_values(players, cfg, min_gp=1, pool_size=8, replacement_mode="flat")}
    hybrid = {r["name"]: r["vor"] for r in
              compute_values(players, cfg, min_gp=1, pool_size=8, replacement_mode="hybrid")}

    # c1 and g1 have identical raw pts -> identical `value` regardless of mode.
    # Flat uses one shared replacement, so their VOR ties.
    assert flat["c1"] == flat["g1"]
    # Hybrid's center replacement is the weak 2nd-best-of-3 center; its guard
    # replacement is the still-strong 2nd-best-of-5 guard. Scarcity should
    # show up as c1 pulling ahead of g1 on VOR despite equal raw value.
    assert hybrid["c1"] > hybrid["g1"]


def test_hybrid_rank_order_can_differ_from_flat_rank_order():
    # c1 (center) has LOWER raw value than g2 (guard) -- flat ranks g2 above
    # c1. But g2 sits exactly at the guard demand cutoff (2nd-best of 5 deep
    # guards), so its own value IS its replacement level -> hybrid VOR 0.
    # c1's center replacement is the weak 2nd-of-3-centers bar, well below
    # its own value -> positive VOR. Scarcity should flip the order.
    players = {
        1: _pts_line("c1", 15, ("C",)), 2: _pts_line("c2", 10, ("C",)), 3: _pts_line("c3", 2, ("C",)),
        4: _pts_line("g1", 20, ("G",)), 5: _pts_line("g2", 17, ("G",)), 6: _pts_line("g3", 14, ("G",)),
        7: _pts_line("g4", 10, ("G",)), 8: _pts_line("g5", 6, ("G",)),
    }
    cfg = _pts_only_cfg([RosterSlot(position="C", count=1), RosterSlot(position="G", count=1),
                         RosterSlot(position="UTIL", count=1)])
    flat = compute_values(players, cfg, min_gp=1, pool_size=8, replacement_mode="flat")
    hybrid = compute_values(players, cfg, min_gp=1, pool_size=8, replacement_mode="hybrid")
    assert rank_of(flat, "c1") > rank_of(flat, "g2")       # flat: g2 (higher raw value) ranks above c1
    assert rank_of(hybrid, "c1") < rank_of(hybrid, "g2")   # hybrid: scarcity flips it -- c1 passes g2


def test_combo_position_uses_most_favorable_eligible_group():
    players = {
        1: _pts_line("c1", 20, ("C",)), 2: _pts_line("c2", 15, ("C",)), 3: _pts_line("c3", 5, ("C",)),
        4: _pts_line("g1", 20, ("G",)), 5: _pts_line("g2", 18, ("G",)), 6: _pts_line("g3", 16, ("G",)),
        7: _pts_line("g4", 14, ("G",)), 8: _pts_line("g5", 12, ("G",)),
        # combo and g6 tie with c3 at the bottom (pts=5), below both groups'
        # demand-2 cutoffs, so they don't themselves distort group_levels.
        9: _pts_line("combo", 5, ("C", "G")),
        10: _pts_line("g6", 5, ("G",)),
    }
    cfg = _pts_only_cfg([RosterSlot(position="C", count=1), RosterSlot(position="G", count=1),
                         RosterSlot(position="UTIL", count=1)])
    board = {r["name"]: r["vor"] for r in
             compute_values(players, cfg, min_gp=1, pool_size=10, replacement_mode="hybrid")}
    # C's replacement (weak 2nd-of-3 centers) is more favorable than G's
    # (strong 2nd-of-5 guards) here, so combo should match a pure center at
    # the same raw value, not a pure guard at that same value.
    assert board["combo"] == pytest.approx(board["c3"])
    assert board["combo"] != pytest.approx(board["g6"])


def test_unknown_position_falls_back_to_flat_replacement():
    players = {
        1: _pts_line("c1", 20, ("C",)), 2: _pts_line("c2", 15, ("C",)), 3: _pts_line("c3", 5, ("C",)),
        4: _pts_line("mystery", 20, ()),  # no position data at all
    }
    cfg = _pts_only_cfg([RosterSlot(position="C", count=1), RosterSlot(position="UTIL", count=1)])
    flat = {r["name"]: r["vor"] for r in
            compute_values(players, cfg, min_gp=1, pool_size=4, replacement_mode="flat")}
    hybrid = {r["name"]: r["vor"] for r in
              compute_values(players, cfg, min_gp=1, pool_size=4, replacement_mode="hybrid")}
    assert hybrid["mystery"] == pytest.approx(flat["mystery"])


def test_points_league_ignores_replacement_mode():
    two = IRONMAN_FRAGILE_FIXTURE
    pl = points_league()
    flat = compute_values(two, pl, min_gp=1, pool_size=2, replacement_mode="flat")
    hybrid = compute_values(two, pl, min_gp=1, pool_size=2, replacement_mode="hybrid")
    assert flat == hybrid


def test_invalid_replacement_mode_rejected():
    with pytest.raises(ValueError):
        compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6, replacement_mode="bogus")


def test_flat_replacement_mode_matches_pre_c2_ranking():
    # Regression: flat must still rank purely by value (order-preserving vor
    # subtraction), reproducing the exact board the recorded backtest baseline
    # was measured against.
    board = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6, replacement_mode="flat")
    by_value = sorted(SIX_PLAYER_FIXTURE, key=lambda pid: next(r["value"] for r in board if r["nba_id"] == pid),
                      reverse=True)
    assert [r["nba_id"] for r in board] == by_value


def test_fgm_ftm_scale_with_availability_like_fga_fta():
    # Classic points-league shape: FGM +2 / FGA -1 / FTM +1 / FTA -1.
    # Per-game differential is nonzero (16 - 15 + 6 - 6 = 1) so a proportional
    # scaling shows up as a clean ratio between the two players' totals.
    line = dict(fgm=8, fga=15, ftm=6, fta=6)
    two = {
        1: dict(name="high_gp", gp=80, **line),
        2: dict(name="low_gp", gp=20, **line),
    }
    cfg = LeagueConfig(
        scoring_type=ScoringType.POINTS,
        num_teams=10,
        point_values={"fgm": 2.0, "fga": -1.0, "ftm": 1.0, "fta": -1.0},
    )
    board = compute_values(two, cfg, min_gp=1, pool_size=2, basis="availability_adjusted")
    v_high = next(r["value"] for r in board if r["name"] == "high_gp")
    v_low = next(r["value"] for r in board if r["name"] == "low_gp")
    expected_ratio = (80 ** 0.5) / (20 ** 0.5)
    assert abs((v_high / v_low) - expected_ratio) < 0.05, (v_high, v_low, expected_ratio)
