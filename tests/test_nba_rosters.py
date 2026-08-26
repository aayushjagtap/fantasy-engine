"""tests/test_nba_rosters.py -- C1: ingest/nba_rosters.py, offline."""
from __future__ import annotations

import pytest

from ingest.nba_boxscores import OfflineCacheMissError
from ingest.nba_rosters import (
    _rows_to_roster,
    attach_positions,
    get_season_rosters,
    load_rosters_or_warn,
    normalize_position,
)

FAKE_ROSTER_RESPONSE = {
    "resultSets": [
        {"name": "Coaches", "headers": ["TEAM_ID", "COACH_NAME"], "rowSet": []},
        {
            "name": "CommonTeamRoster",
            "headers": ["TeamID", "SEASON", "PLAYER", "NUM", "POSITION", "AGE", "PLAYER_ID"],
            "rowSet": [
                [1610612747, "2025-26", "LeBron James", "23", "Forward", 40, 2544],
                [1610612747, "2025-26", "Guard-Forward Guy", "0", "Guard-Forward", 25, 999001],
                [1610612747, "2025-26", "No Position Guy", "1", "", 22, 999002],
            ],
        },
    ]
}


# --- normalize_position ---

def test_normalize_position_spelled_out():
    assert normalize_position("Guard") == ("G",)
    assert normalize_position("Forward") == ("F",)
    assert normalize_position("Center") == ("C",)


def test_normalize_position_combo_spelled_out():
    assert normalize_position("Guard-Forward") == ("F", "G")


def test_normalize_position_abbreviated():
    assert normalize_position("G") == ("G",)
    assert normalize_position("F-C") == ("C", "F")


def test_normalize_position_missing_returns_empty():
    assert normalize_position(None) == ()
    assert normalize_position("") == ()


# --- _rows_to_roster ---

def test_rows_to_roster_keys_by_player_id_and_normalizes_position():
    roster = _rows_to_roster(FAKE_ROSTER_RESPONSE, "LAL")
    assert roster[2544] == {"position": ("F",), "team": "LAL"}
    assert roster[999001] == {"position": ("F", "G"), "team": "LAL"}
    assert roster[999002] == {"position": (), "team": "LAL"}


def test_rows_to_roster_finds_result_set_by_name_not_index():
    # Coaches (empty rowSet) is listed FIRST -- index 0 would be wrong.
    roster = _rows_to_roster(FAKE_ROSTER_RESPONSE, "LAL")
    assert len(roster) == 3


# --- get_season_rosters: caching + offline behavior (mirrors test_nba_boxscores.py) ---

def test_get_season_rosters_caches_one_file_per_team(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("ingest.nba_rosters._team_list", lambda: [(1, "AAA"), (2, "BBB")])
    calls = {"n": 0}

    def fake_fetch(team_id, season):
        calls["n"] += 1
        return {
            "resultSets": [{
                "name": "CommonTeamRoster",
                "headers": ["PLAYER_ID", "POSITION"],
                "rowSet": [[100 + team_id, "Center"]],
            }]
        }

    monkeypatch.setattr("ingest.nba_rosters._fetch_team_roster", fake_fetch)
    players, stats = get_season_rosters("2025-26")
    assert calls["n"] == 2
    assert stats == {"season": "2025-26", "teams_total": 2, "teams_with_data": 2}
    assert players[101]["team"] == "AAA"
    assert players[102]["team"] == "BBB"

    # Second call should be served entirely from cache -- no new fetches.
    get_season_rosters("2025-26")
    assert calls["n"] == 2


def test_get_season_rosters_offline_miss_fails_fast(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("ingest.nba_rosters._team_list", lambda: [(1, "AAA")])

    def fail_if_called(team_id, season):
        raise AssertionError("network should not be attempted in offline mode")

    monkeypatch.setattr("ingest.nba_rosters._fetch_team_roster", fail_if_called)
    with pytest.raises(OfflineCacheMissError):
        get_season_rosters("2025-26", offline=True)


# --- load_rosters_or_warn: the "no cache" degradation path ---
# tests/test_cli.py and test_validate.py monkeypatch _load_players / higher-level
# loaders wholesale, so this fallback (which cli/board.py and backtest/validate.py
# both lean on) is only exercised here.

def _empty_cache(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("ingest.nba_rosters._team_list", lambda: [(1, "AAA")])

    def fail_if_called(team_id, season):
        raise AssertionError("offline degradation must not attempt the network")

    monkeypatch.setattr("ingest.nba_rosters._fetch_team_roster", fail_if_called)


def test_load_rosters_or_warn_degrades_to_empty_map_with_stderr_notice(monkeypatch, tmp_path, capsys):
    _empty_cache(monkeypatch, tmp_path)
    result = load_rosters_or_warn()
    assert result == {}
    err = capsys.readouterr().err
    assert "no cached position/team data" in err
    assert "ingest/nba_rosters.py" in err


def test_load_rosters_or_warn_quiet_suppresses_the_notice(monkeypatch, tmp_path, capsys):
    _empty_cache(monkeypatch, tmp_path)
    assert load_rosters_or_warn(quiet=True) == {}
    assert capsys.readouterr().err == ""


# --- attach_positions ---

def test_attach_positions_merges_position_and_prefers_roster_team():
    players = {
        1: {"name": "has_roster", "team": "OLD"},
        2: {"name": "no_roster", "team": "STAYS"},
    }
    rosters = {1: {"position": ("G",), "team": "NEW"}}
    out = attach_positions(players, rosters=rosters)
    assert out[1]["position"] == ("G",)
    assert out[1]["team"] == "NEW"
    assert out[2]["position"] == ()
    assert out[2]["team"] == "STAYS"  # no roster match -> team left untouched


def test_attach_positions_does_not_mutate_input():
    players = {1: {"name": "p", "team": "OLD"}}
    attach_positions(players, rosters={1: {"position": ("C",), "team": "NEW"}})
    assert players[1] == {"name": "p", "team": "OLD"}
