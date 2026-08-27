"""tests/test_team.py -- E1, the roster-state object (engine/team.py).

Offline, synthetic fixtures only. Covers:
  * ratio categories aggregate by volume, not by averaging percentages
  * counting categories sum
  * the league baseline == the synthetic-team partition it documents
  * snake vs random baseline give different sigma_c (sensitivity is real)
  * standing z reflects roster shape (stacked category -> positive z)
  * standing() does not clamp z by default; a float z_cap restores it, and
    under a cap marginal_value goes exactly 0 in pinned categories
  * mid-draft proration compares a k-roster to the synthetic teams' first k
    picks, not a linear k/total-slots slice
  * expected_category_wins() raises on an empty roster (n_active/2 is meaningless)
  * diminishing returns: the same player is worth less in a category the
    roster already dominates
  * marginal_value is internally consistent (per_category sums to the delta,
    before/after match the objective)
  * h2h and roto units are an affine transform -> identical rankings
  * matchup_format defaults to h2h and round-trips; leagues/roto_8cat.json is roto
  * detect_punts reports without mutating the config
  * PUNT SYMMETRY: a 90% FT shooter is worth materially less to a de-facto
    punt-FT% roster than to a balanced one (the SCOPE.md motivating claim)
  * points leagues are rejected; empty rosters do not blow up
  * engine/ imports nothing from cli/
"""
from __future__ import annotations

import math
import pathlib
import re

import pytest

from engine.league_config import (
    CATEGORY_META, CategorySetting, LeagueConfig, MatchupFormat, RosterSlot,
    ScoringType, StatCategory, points_league,
)
from engine.team import PUNT_Z, Team, _team_cat_total

SC = StatCategory


def _p(name, pts, reb, ast, stl, blk, fg3m, tov, fg_pct, fga, ft_pct, fta, gp=72):
    return dict(
        name=name, gp=gp, min=32.0, pts=pts, reb=reb, ast=ast, stl=stl, blk=blk,
        fg3m=fg3m, tov=tov,
        fg_pct=fg_pct, fga=fga, fgm=round(fg_pct * fga, 3),
        ft_pct=ft_pct, fta=fta, ftm=round(ft_pct * fta, 3),
    )


# 16 players spanning tiers and profiles. ids are stable handles used below.
STAR_A, STAR_B = 1, 2
WALL_A, WALL_B, WALL_C = 3, 4, 5            # elite blk + reb, shaky ft
GUARD_A, GUARD_B, GUARD_C = 6, 7, 8         # ast/stl/fg3m, high ft
FT_ACE = 9                                  # 92% ft on real volume
MID_A, MID_B, MID_C, MID_D = 10, 11, 12, 13
BIG_X, BIG_Y, BIG_Z = 14, 15, 16           # low-ft interior bigs

POOL = {
    STAR_A:  _p("star_a", 28, 8, 7, 1.5, 1.0, 2.5, 3.0, 0.50, 19, 0.85, 7),
    STAR_B:  _p("star_b", 26, 10, 6, 1.3, 1.4, 1.8, 2.8, 0.52, 18, 0.80, 7),
    WALL_A:  _p("wall_a", 19, 12, 2.0, 0.8, 3.2, 0.2, 2.0, 0.58, 13, 0.58, 8),
    WALL_B:  _p("wall_b", 17, 12, 1.5, 0.7, 2.9, 0.1, 1.8, 0.60, 12, 0.55, 9),
    WALL_C:  _p("wall_c", 16, 11, 1.8, 0.9, 2.7, 0.3, 1.9, 0.59, 12, 0.60, 8),
    GUARD_A: _p("guard_a", 22, 4, 9, 1.8, 0.3, 3.2, 3.1, 0.45, 17, 0.88, 5),
    GUARD_B: _p("guard_b", 20, 3.5, 8, 1.6, 0.2, 3.0, 2.6, 0.44, 16, 0.90, 4),
    GUARD_C: _p("guard_c", 18, 3, 7, 1.9, 0.2, 2.6, 2.2, 0.43, 15, 0.86, 3.5),
    FT_ACE:  _p("ft_ace", 19, 5, 4, 1.1, 0.4, 2.2, 1.8, 0.47, 14, 0.92, 8),
    MID_A:   _p("mid_a", 15, 7, 4, 1.0, 0.8, 1.5, 1.7, 0.49, 12, 0.78, 3.5),
    MID_B:   _p("mid_b", 14, 6.5, 3.5, 0.9, 0.7, 1.4, 1.6, 0.48, 11, 0.80, 3),
    MID_C:   _p("mid_c", 13, 6, 5, 1.1, 0.5, 1.2, 1.9, 0.47, 11, 0.79, 3),
    MID_D:   _p("mid_d", 12, 5.5, 3, 0.8, 0.6, 1.0, 1.4, 0.46, 10, 0.81, 2.5),
    BIG_X:   _p("big_x", 15, 9, 1.5, 0.6, 1.6, 0.1, 1.7, 0.56, 11, 0.60, 8),
    BIG_Y:   _p("big_y", 14, 8.5, 1.2, 0.5, 1.5, 0.1, 1.6, 0.57, 10, 0.56, 8),
    BIG_Z:   _p("big_z", 13, 8, 1.4, 0.7, 1.4, 0.2, 1.5, 0.55, 10, 0.60, 6),
}


def _cfg(*, matchup="h2h", num_teams=3, slots=3):
    return LeagueConfig(
        name="test",
        scoring_type=ScoringType.CATEGORY,
        num_teams=num_teams,
        matchup_format=matchup,
        categories={
            c: CategorySetting() for c in (
                SC.PTS, SC.REB, SC.AST, SC.STL, SC.BLK, SC.FG3M, SC.TOV,
                SC.FG_PCT, SC.FT_PCT,
            )
        },
        roster=[RosterSlot(position="UTIL", count=slots)],
    )


def _team(roster_ids, *, basis="availability_adjusted", baseline_method="snake",
          matchup="h2h", num_teams=3, slots=3):
    return Team(_cfg(matchup=matchup, num_teams=num_teams, slots=slots),
                POOL, roster_ids, min_gp=1, basis=basis,
                baseline_method=baseline_method)


# --------------------------------------------------------------------------- #
# ratio categories do not sum
# --------------------------------------------------------------------------- #

def test_ratio_category_is_volume_weighted_not_mean():
    # low-volume 95% shooter + high-volume 60% shooter
    lo = _p("lo_vol_high_ft", 10, 3, 2, 0.5, 0.2, 1.0, 1.0, 0.5, 6, 0.95, 2)
    hi = _p("hi_vol_low_ft", 20, 6, 3, 0.8, 0.4, 1.5, 2.0, 0.5, 12, 0.60, 10)
    players = {101: lo, 102: hi}
    t = Team(_cfg(), players, [101, 102], min_gp=1, basis="per_game")
    got = t.category_totals()[SC.FT_PCT]
    expected = (0.95 * 2 + 0.60 * 10) / (2 + 10)          # makes / attempts
    naive_mean = (0.95 + 0.60) / 2
    assert got == pytest.approx(expected, abs=1e-9)
    assert abs(got - naive_mean) > 0.11                    # and it is NOT the mean


def test_ratio_total_is_scale_invariant_to_basis():
    # makes and attempts scale by the same availability factor, so the ratio
    # is identical under per_game and availability_adjusted.
    pg = _team([WALL_A, GUARD_A, FT_ACE], basis="per_game").category_totals()
    aa = _team([WALL_A, GUARD_A, FT_ACE], basis="availability_adjusted").category_totals()
    assert aa[SC.FT_PCT] == pytest.approx(pg[SC.FT_PCT], abs=1e-9)
    assert aa[SC.FG_PCT] == pytest.approx(pg[SC.FG_PCT], abs=1e-9)
    # a counting cat, by contrast, is NOT scale invariant
    assert aa[SC.REB] > pg[SC.REB] * 2


def test_counting_category_totals_sum():
    ids = [STAR_A, WALL_A, GUARD_A]
    t = _team(ids, basis="per_game")
    assert t.category_totals()[SC.REB] == pytest.approx(
        sum(POOL[i]["reb"] for i in ids))
    assert t.category_totals()[SC.TOV] == pytest.approx(
        sum(POOL[i]["tov"] for i in ids))


# --------------------------------------------------------------------------- #
# the league baseline
# --------------------------------------------------------------------------- #

def test_league_baseline_matches_synthetic_partition():
    from engine.value import _mean_std

    t = _team([])
    base = t.league_baseline()
    teams = t._synthetic_teams()
    assert len(teams) == t.config.num_teams
    for cat in t.config.active_categories:
        totals = [_team_cat_total(t._scaled, grp, cat) for grp in teams]
        assert base[cat] == pytest.approx(_mean_std(totals), abs=1e-9)
    assert base[SC.PTS][1] > 0          # real spread in a value-correlated cat


def test_snake_and_random_baselines_differ():
    snake = _team([], baseline_method="snake").league_baseline()
    rand = _team([], baseline_method="random").league_baseline()
    # different partitions -> different spread estimates
    assert snake[SC.PTS][1] != pytest.approx(rand[SC.PTS][1], abs=1e-9)
    # snake deals a value-sorted pool evenly, so it should not OVERstate the
    # spread of the most value-correlated category relative to a random deal.
    assert snake[SC.PTS][1] <= rand[SC.PTS][1] + 1e-9
    for m in ("snake", "random"):
        b = _team([], baseline_method=m).league_baseline()
        assert all(sigma > 0 for _, sigma in b.values())


def test_baseline_method_must_be_valid():
    with pytest.raises(ValueError):
        _team([], baseline_method="kmeans")


# --------------------------------------------------------------------------- #
# standing reflects roster shape
# --------------------------------------------------------------------------- #

def test_stacked_category_shows_positive_z():
    t = _team([WALL_A, WALL_B, WALL_C])            # three elite rebounders
    st = t.standing()
    assert st[SC.REB]["z"] > 0.8
    assert st[SC.REB]["win_prob"] > 0.65
    assert st[SC.AST]["z"] < 0                     # ... and thin on assists


def test_turnovers_are_lower_is_better():
    # a roster that racks up turnovers should be LOSING the tov category (z < 0)
    heavy = _team([STAR_A, GUARD_A, GUARD_B])      # tov 3.0 / 3.1 / 2.6
    light = _team([MID_D, BIG_Z, WALL_B])          # tov 1.4 / 1.5 / 1.8
    assert heavy.standing()[SC.TOV]["z"] < light.standing()[SC.TOV]["z"]


def test_empty_roster_has_no_standing_and_first_pick_still_helps():
    t = _team([])
    st = t.standing()
    # zero picks -> no standing to report: every z is neutral, nothing blows up
    for cat, row in st.items():
        assert row["z"] == 0.0
        assert row["win_prob"] == pytest.approx(0.5)
    mv = t.marginal_value(STAR_A)                 # n_ref floored at 1, no div-by-zero
    assert mv["delta_expected_wins"] > 0
    assert math.isfinite(mv["delta_expected_wins"])


def test_expected_category_wins_raises_on_empty_roster():
    # n_active/2 (every category a coin flip) is not a meaningful objective value
    with pytest.raises(ValueError):
        _team([]).expected_category_wins()
    with pytest.raises(ValueError):
        _team([STAR_A]).expected_category_wins(drop_ids=(STAR_A,))
    # a single real player is fine
    assert _team([STAR_A]).expected_category_wins() > 0


# --------------------------------------------------------------------------- #
# no z-cap on the win-probability path
# --------------------------------------------------------------------------- #

def test_standing_z_is_uncapped_by_default_and_z_cap_restores_clamp():
    ids = [WALL_A, WALL_B, WALL_C]                 # three elite reb/blk bigs -> lopsided
    uncapped = _team(ids).standing()
    assert max(abs(row["z"]) for row in uncapped.values()) > 3.0
    capped = Team(_cfg(), POOL, ids, min_gp=1, z_cap=3.0).standing()
    assert all(abs(row["z"]) <= 3.0 + 1e-9 for row in capped.values())


def test_cap_zeroes_marginal_value_in_pinned_categories():
    """Why the cap is gone: a category pinned at +/-z_cap contributes EXACTLY 0
    to marginal_value (both endpoints clamp to the same value -> same win_prob),
    so a capped Team decides recommendations on only the unpinned categories.
    Uncapped, every category carries gradient."""
    ids = [WALL_A, WALL_B, WALL_C]
    capped = Team(_cfg(), POOL, ids, min_gp=1, z_cap=3.0)
    uncapped = Team(_cfg(), POOL, ids, min_gp=1)                 # z_cap=None default
    zeros_capped = [c for c, v in capped.marginal_value(MID_A)["per_category"].items() if v == 0.0]
    zeros_uncapped = [c for c, v in uncapped.marginal_value(MID_A)["per_category"].items() if v == 0.0]
    assert len(zeros_capped) >= 3                                # cap blinds it to pinned cats
    assert len(zeros_uncapped) < len(zeros_capped)               # dropping the cap restores gradient
    assert set(zeros_uncapped) < set(zeros_capped)               # the survivors are a strict subset


# --------------------------------------------------------------------------- #
# mid-draft proration: first-k picks, not a linear slice
# --------------------------------------------------------------------------- #

def test_proration_uses_synthetic_first_k_not_linear_slice():
    """A k-player roster is compared to the synthetic teams' first k picks, not a
    linear k/team-size slice of a full team. Because early picks are the best
    picks, the first-k mean of a value-correlated category sits ABOVE the linear
    slice -- which is why a category-stacked partial roster stops reading as an
    inflated positive z (see the module docstring and the demo's pts flip)."""
    t = _team([STAR_A, WALL_A])
    firstk = t._prorated_baseline(2)
    full = t.league_baseline()
    team_size = max(len(grp) for grp in t._synthetic_teams())

    # pts is the most value-correlated cat: its first-2-picks mean must exceed
    # the naive linear slice the old code used.
    assert firstk[SC.PTS][0] > full[SC.PTS][0] * (2 / team_size)
    # ratio cats are rates, not prorated -- passed straight through
    assert firstk[SC.FG_PCT] == full[SC.FG_PCT]
    assert firstk[SC.FT_PCT] == full[SC.FT_PCT]
    # it is genuinely a different baseline, not a rescale of the full one
    assert firstk[SC.PTS] != full[SC.PTS]
    # once k reaches the synthetic team size there is nothing to truncate
    assert t._prorated_baseline(team_size) == full
    assert t._prorated_baseline(team_size + 5) == full


def test_marginal_value_sums_and_matches_objective():
    for units in ("category_wins", "roto_points"):
        t = _team([STAR_A, WALL_A, GUARD_A])
        mv = t.marginal_value(FT_ACE, units=units)
        n_ref = len(t.roster_ids) + 1                 # marginal_value's shared yardstick
        assert mv["before"] == pytest.approx(
            t.expected_category_wins(units=units, prorate_to=n_ref), abs=1e-9)
        assert mv["after"] == pytest.approx(
            t.expected_category_wins(extra_ids=(FT_ACE,), units=units,
                                     prorate_to=n_ref), abs=1e-9)
        assert sum(mv["per_category"].values()) == pytest.approx(
            mv["delta_expected_wins"], abs=1e-9)
        assert mv["after"] - mv["before"] == pytest.approx(
            mv["delta_expected_wins"], abs=1e-9)


def test_add_remove_mutate_roster_without_touching_baseline():
    t = _team([STAR_A])
    base_before = dict(t.league_baseline())
    t.add(WALL_A)
    assert t.roster_ids == {STAR_A, WALL_A}
    t.remove(STAR_A)
    assert t.roster_ids == {WALL_A}
    assert t.league_baseline() == base_before      # baseline is pool-level, not roster-level


# --------------------------------------------------------------------------- #
# h2h vs roto: affine transform, identical ranking
# --------------------------------------------------------------------------- #

def test_roto_points_is_affine_transform_of_category_wins():
    t = _team([STAR_A, WALL_A, GUARD_A, FT_ACE])
    n_active = len(t.config.active_categories)
    n_teams = t.config.num_teams
    h2h = t.expected_category_wins(units="category_wins")
    roto = t.expected_category_wins(units="roto_points")
    assert roto == pytest.approx(n_active + (n_teams - 1) * h2h, abs=1e-9)


def test_units_rank_candidates_identically():
    t = _team([STAR_A, WALL_A])
    cands = [GUARD_A, GUARD_B, WALL_C, MID_A, FT_ACE, BIG_X]
    by_h2h = sorted(cands, key=lambda c: t.marginal_value(c, units="category_wins")
                    ["delta_expected_wins"])
    by_roto = sorted(cands, key=lambda c: t.marginal_value(c, units="roto_points")
                     ["delta_expected_wins"])
    assert by_h2h == by_roto
    # and per-category deltas differ only by the (num_teams - 1) slope
    scale = t.config.num_teams - 1
    mv_h = t.marginal_value(GUARD_A, units="category_wins")["per_category"]
    mv_r = t.marginal_value(GUARD_A, units="roto_points")["per_category"]
    for cat in mv_h:
        assert mv_r[cat] == pytest.approx(scale * mv_h[cat], abs=1e-9)


def test_units_default_follows_matchup_format():
    roto_team = _team([STAR_A, WALL_A], matchup="roto")
    assert roto_team.expected_category_wins() == pytest.approx(
        roto_team.expected_category_wins(units="roto_points"), abs=1e-9)
    h2h_team = _team([STAR_A, WALL_A], matchup="h2h")
    assert h2h_team.expected_category_wins() == pytest.approx(
        h2h_team.expected_category_wins(units="category_wins"), abs=1e-9)


def test_bad_units_rejected():
    with pytest.raises(ValueError):
        _team([STAR_A]).expected_category_wins(units="fantasy_points")


# --------------------------------------------------------------------------- #
# matchup_format on the config
# --------------------------------------------------------------------------- #

def test_matchup_format_defaults_to_h2h_and_round_trips(tmp_path):
    cfg = _cfg()
    assert cfg.matchup_format is MatchupFormat.H2H
    path = tmp_path / "c.json"
    cfg.to_json(path)
    assert LeagueConfig.load(path).matchup_format is MatchupFormat.H2H
    roto = _cfg(matchup="roto")
    roto.to_json(path)
    assert LeagueConfig.load(path).matchup_format is MatchupFormat.ROTO


def test_shipped_roto_league_file_declares_roto():
    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = LeagueConfig.load(root / "leagues" / "roto_8cat.json")
    assert cfg.matchup_format is MatchupFormat.ROTO


# --------------------------------------------------------------------------- #
# punt detection
# --------------------------------------------------------------------------- #

def test_detect_punts_reports_without_mutating_config():
    cfg = _cfg()
    t = Team(cfg, POOL, [WALL_A, WALL_B, BIG_X, BIG_Y], min_gp=1)
    punts = t.detect_punts()
    assert SC.FT_PCT in punts
    assert all(z <= PUNT_Z for z in punts.values())
    # config is untouched -- an inferred punt is never written back
    assert cfg.punted_categories == []
    assert cfg.categories[SC.FT_PCT].enabled is True
    assert SC.FT_PCT in cfg.active_categories


def test_detect_punts_threshold_is_adjustable():
    t = _team([WALL_A, WALL_B, BIG_X, BIG_Y])
    strict = t.detect_punts(threshold=-3.0)
    loose = t.detect_punts(threshold=-0.5)
    assert set(strict) <= set(loose)


# --------------------------------------------------------------------------- #
# PUNT SYMMETRY -- the claim SCOPE.md justifies this phase with
# --------------------------------------------------------------------------- #

def _clone(name, ft_pct, fta):
    """League-average profile, varying only free-throw shooting."""
    return _p(name, pts=15, reb=6, ast=3, stl=1.0, blk=0.6, fg3m=1.2, tov=1.6,
              fg_pct=0.48, fga=12, ft_pct=ft_pct, fta=fta)


def test_ft_shooter_worth_less_to_a_punt_ft_roster():
    """The SCOPE.md motivating claim. Two rosters identical in every category
    except free-throw shooting: one already buried in FT%, one league-average.
    Adding a 90% FT shooter helps the buried roster materially less -- it sits on
    the flat tail of the win-probability curve, the balanced roster on its steep
    middle. Because the rosters match everywhere else, every non-FT category
    delta is identical and FT% is the only thing that moves the totals apart."""
    pool = dict(POOL)                       # real spread -> a sane FT% baseline
    # eight-player rosters so one addition can't swing the team ratio much: the
    # buried roster is still buried after adding the shooter.
    punt_ids = list(range(201, 209))
    bal_ids = list(range(211, 219))
    for i in punt_ids:
        pool[i] = _clone(f"punt{i}", 0.44, 9)     # deep in the conceded tail
    for i in bal_ids:
        pool[i] = _clone(f"bal{i}", 0.75, 9)      # ~ the league FT% mean: steepest part
    pool[220] = _clone("ft_ace", 0.92, 9)

    cfg = _cfg(num_teams=6, slots=3)
    punt_ft = Team(cfg, pool, punt_ids, min_gp=1)
    balanced = Team(cfg, pool, bal_ids, min_gp=1)
    mv_punt = punt_ft.marginal_value(220)
    mv_bal = balanced.marginal_value(220)

    # rosters are identical outside FT%, so every non-FT delta matches exactly
    for cat in cfg.active_categories:
        if cat is SC.FT_PCT:
            continue
        assert mv_punt["per_category"][cat] == pytest.approx(
            mv_bal["per_category"][cat], abs=1e-9)

    # the buried roster is so far behind in FT% that the 90% shooter moves its
    # category-win probability essentially not at all; the balanced roster,
    # sitting on the steep middle of the curve, gains materially.
    ft_punt = mv_punt["per_category"][SC.FT_PCT]
    ft_bal = mv_bal["per_category"][SC.FT_PCT]
    assert 0 <= ft_punt < 0.25 * ft_bal
    assert mv_punt["delta_expected_wins"] < mv_bal["delta_expected_wins"]


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #

def test_points_league_is_rejected():
    with pytest.raises(ValueError):
        Team(points_league(), POOL, [], min_gp=1)


def test_player_breakdown_reuses_category_contributions():
    t = _team([STAR_A, WALL_A, GUARD_A])
    bd = t.player_breakdown()
    assert set(bd) == {STAR_A, WALL_A, GUARD_A}
    row = bd[WALL_A]
    assert set(row) == set(t.config.active_categories)
    # wall_a's blocks should be a positive contributor vs the pool
    assert row[SC.BLK]["z"] > 0


def test_engine_modules_do_not_import_cli():
    engine_dir = pathlib.Path(__file__).resolve().parents[1] / "engine"
    offenders = [
        f.name for f in engine_dir.glob("*.py")
        if re.search(r"^\s*(from|import)\s+cli(\.|\s|$)", f.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []
