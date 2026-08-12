"""tests/test_diagnose.py -- moved from engine/diagnose.py's --selftest."""
from __future__ import annotations

from engine.diagnose import explain
from engine.league_config import standard_9cat
from engine.value import compute_values
from tests.fixtures import SIX_PLAYER_FIXTURE


def test_explain_contributions_sum_to_board_value():
    rank, total = explain(SIX_PLAYER_FIXTURE, standard_9cat(), "allround", min_gp=1, pool_size=6)
    board = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6)
    board_value = next(r["value"] for r in board if r["name"] == "allround")
    assert abs(total - board_value) < 0.02, (total, board_value)


def test_explain_unknown_player_returns_none():
    rank, total = explain(SIX_PLAYER_FIXTURE, standard_9cat(), "nobody_by_this_name", min_gp=1, pool_size=6)
    assert rank is None
    assert total is None
