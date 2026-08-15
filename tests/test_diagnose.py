"""tests/test_diagnose.py -- moved from engine/diagnose.py's --selftest."""
from __future__ import annotations

import pytest

from engine.diagnose import explain
from engine.league_config import standard_9cat
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
