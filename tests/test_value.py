"""tests/test_value.py -- moved from engine/value.py's --selftest, plus new
regression coverage for the SCALE_FIELDS bug (A3) and the basis rename (A5).
"""
from __future__ import annotations

from engine.league_config import (
    LeagueConfig,
    ScoringType,
    points_league,
    punt_ft_9cat,
    standard_9cat,
)
from engine.value import SCALE_FIELDS, compute_values
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
