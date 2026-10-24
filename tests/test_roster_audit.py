"""Offline roster and player coverage audit regressions."""
from __future__ import annotations

from ingest.roster_audit import build_audit


def test_audit_separates_current_fallback_history_and_missing_positions():
    current = {
        1: {"name": "Veteran", "team": "AAA", "position": ("G",)},
        2: {"name": "New Player", "team": "BBB", "position": ()},
        3: {"name": "No History", "team": "CCC", "position": ("F",)},
    }
    fallback = {
        1: {"name": "Veteran", "team": "AAA", "position": ("G",)},
        4: {"name": "Fallback Only", "team": "DDD", "position": ("C",)},
    }
    report = build_audit(
        current, fallback,
        [{"team": "AAA", "has_data": True, "player_count": 1}],
        [{"team": "AAA", "has_data": True, "player_count": 1}],
        {4: {"name": "Fallback Only"}},
        {"2025-26": {4: {"name": "Fallback Only"}},
         "2024-25": {1: {"name": "Veteran"}}},
        "2025-26", "2026-27",
    )

    assert report["current_count"] == 3
    assert report["fallback_count"] == 2
    assert report["merged_count"] == 4
    assert [p["nba_id"] for p in report["fallback_only"]] == [4]
    assert [p["nba_id"] for p in report["missing_positions"]] == [2]
    assert [p["nba_id"] for p in report["with_older_history"]] == [1]
    assert [p["nba_id"] for p in report["without_history"]] == [2, 3]
    assert report["with_older_history"][0]["history_seasons"] == ["2024-25"]
