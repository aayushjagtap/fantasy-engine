"""tests/test_diagnose.py -- moved from engine/diagnose.py's --selftest."""
from __future__ import annotations

import pytest

from engine.diagnose import category_contributions, explain
from engine.league_config import points_league, standard_9cat
from engine.value import compute_values
from tests.fixtures import SIX_PLAYER_FIXTURE

# A fixture with an accented name for the normalize_name-based lookup (B2):
# exact match must be accent/case-insensitive so "Luka Doncic" finds "Luka Dončić".
ACCENTED_FIXTURE = dict(SIX_PLAYER_FIXTURE)
ACCENTED_FIXTURE[99] = dict(
    name="Luka Dončić", gp=70, pts=25, reb=8, ast=9, stl=1.2, blk=0.5,
    tov=3.0, fg3m=3.0, fg_pct=0.5, fga=20, ft_pct=0.78, fta=8,
)


def test_explain_contributions_sum_to_board_value():
    rank, total = explain(SIX_PLAYER_FIXTURE, standard_9cat(), "allround", min_gp=1, pool_size=6)
    board = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6)
    board_value = next(r["value"] for r in board if r["name"] == "allround")
    assert abs(total - board_value) < 0.02, (total, board_value)


def test_explain_unknown_player_returns_none():
    rank, total = explain(SIX_PLAYER_FIXTURE, standard_9cat(), "nobody_by_this_name", min_gp=1, pool_size=6)
    assert rank is None
    assert total is None


# --- B2: accent-insensitive lookup (normalize_name three-tier match) ---

@pytest.mark.parametrize("query", ["Luka Dončić", "Luka Doncic", "luka doncic", "LUKA DONCIC"])
def test_explain_accent_insensitive_exact_match_resolves(query):
    rank, total = explain(ACCENTED_FIXTURE, standard_9cat(), query, min_gp=1, pool_size=7)
    assert rank is not None
    assert total is not None


def test_explain_partial_surname_suggests_but_does_not_resolve(capsys):
    rank, total = explain(ACCENTED_FIXTURE, standard_9cat(), "Doncic", min_gp=1, pool_size=7)
    out = capsys.readouterr().out
    assert rank is None
    assert total is None
    assert "Luka Dončić" in out


def test_explain_near_miss_did_you_mean_still_works(capsys):
    rank, total = explain(SIX_PLAYER_FIXTURE, standard_9cat(), "round", min_gp=1, pool_size=6)
    out = capsys.readouterr().out
    assert rank is None
    assert "allround" in out


# --- category_contributions: the per-category machinery explain() now sits on,
# exposed as data for engine/divergence.py (M5). ---

def _tid(name):
    return next(i for i, p in SIX_PLAYER_FIXTURE.items() if p["name"] == name)


def test_category_contributions_sum_matches_board_value():
    contribs = category_contributions(SIX_PLAYER_FIXTURE, standard_9cat(), _tid("allround"),
                                      min_gp=1, pool_size=6)
    board = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6)
    board_value = next(r["value"] for r in board if r["name"] == "allround")
    assert abs(sum(c["z"] for c in contribs.values()) - board_value) < 0.02


def test_category_contributions_keys_and_shape():
    cfg = standard_9cat()
    contribs = category_contributions(SIX_PLAYER_FIXTURE, cfg, _tid("sharp"), min_gp=1, pool_size=6)
    assert set(contribs) == set(cfg.active_categories)
    row = contribs[next(iter(contribs))]
    assert set(row) == {"z", "capped", "player", "pool_avg", "is_ratio", "volume_stat"}


def test_category_contributions_none_for_points_league_and_ineligible():
    assert category_contributions(SIX_PLAYER_FIXTURE, points_league(), _tid("sharp"),
                                  min_gp=1, pool_size=6) is None
    assert category_contributions(SIX_PLAYER_FIXTURE, standard_9cat(), 999999,
                                  min_gp=1, pool_size=6) is None


def test_category_contributions_pool_ids_override_pins_baseline():
    """Passing pool_ids fixes the baseline pool: two different player lines scored
    against the SAME pool_ids differ only where the line differs. This is what
    engine/divergence.py relies on to make its z-deltas mean 'the player moved'."""
    cfg = standard_9cat()
    pool_ids = list(SIX_PLAYER_FIXTURE)
    tid = _tid("wing")
    base = category_contributions(SIX_PLAYER_FIXTURE, cfg, tid, min_gp=1, pool_ids=pool_ids)
    bumped_players = dict(SIX_PLAYER_FIXTURE)
    bumped_players[tid] = dict(SIX_PLAYER_FIXTURE[tid], reb=SIX_PLAYER_FIXTURE[tid]["reb"] + 6)
    bumped = category_contributions(bumped_players, cfg, tid, min_gp=1, pool_ids=pool_ids)
    from engine.league_config import StatCategory as SC
    assert bumped[SC.REB]["z"] > base[SC.REB]["z"]
    assert abs(bumped[SC.AST]["z"] - base[SC.AST]["z"]) < 1e-9  # untouched cat unchanged
