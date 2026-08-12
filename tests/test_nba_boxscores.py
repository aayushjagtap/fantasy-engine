"""tests/test_nba_boxscores.py -- moved from ingest/nba_boxscores.py's --selftest."""
from __future__ import annotations

import datetime
import tempfile

import pytest

from ingest.nba_boxscores import (
    OfflineCacheMissError,
    _cached,
    _rows_to_players,
    get_season_boxscores,
    recent_completed_seasons,
)


def test_rows_to_players_keys_by_nba_id():
    fake = {
        "resultSets": [{
            "name": "LeagueDashPlayerStats",
            "headers": ["PLAYER_ID", "PLAYER_NAME", "GP", "MIN", "PTS", "REB",
                        "AST", "STL", "BLK", "TOV", "FG3M", "FGM", "FGA",
                        "FG_PCT", "FTM", "FTA", "FT_PCT"],
            "rowSet": [
                [2544, "LeBron James", 71, 35.3, 25.7, 7.3, 8.3, 1.3, 0.5, 3.5,
                 2.1, 9.5, 18.0, 0.540, 4.6, 6.1, 0.750],
                [201939, "Stephen Curry", 74, 32.7, 26.4, 4.5, 5.1, 0.7, 0.4, 2.8,
                 4.8, 9.0, 19.5, 0.450, 4.2, 4.5, 0.915],
            ],
        }]
    }
    players = _rows_to_players(fake)
    assert set(players) == {2544, 201939}, players.keys()
    assert players[2544]["name"] == "LeBron James"
    assert players[2544]["pts"] == 25.7
    assert players[201939]["fg3m"] == 4.8


def test_recent_completed_seasons_is_date_driven():
    assert recent_completed_seasons(3, datetime.date(2026, 7, 4)) == \
        ["2025-26", "2024-25", "2023-24"]
    # Mid-season (Jan) -> current season not yet complete, so latest is the prior one.
    assert recent_completed_seasons(2, datetime.date(2026, 1, 15)) == \
        ["2024-25", "2023-24"]


def test_recent_completed_seasons_pinned_to_2026_08_11():
    # Pinned regression: season derivation must not silently drift as "today" moves.
    assert recent_completed_seasons(3, datetime.date(2026, 8, 11)) == \
        ["2025-26", "2024-25", "2023-24"]


def test_cache_serves_from_disk_after_first_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"payload": calls["n"]}

    r1, s1 = _cached("k", fetch)
    r2, s2 = _cached("k", fetch)
    assert (s1, s2) == ("network", "cache"), (s1, s2)
    assert calls["n"] == 1, "cache should prevent a second fetch"
    assert r1 == r2


def test_offline_cache_hit_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))
    _cached("k", lambda: {"payload": 1})  # populate cache first (network allowed here)

    def fail_if_called():
        raise AssertionError("network should not be attempted when the key is cached")

    raw, src = _cached("k", fail_if_called, offline=True)
    assert (raw, src) == ({"payload": 1}, "cache")


def test_offline_cache_miss_fails_fast_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))

    def fail_if_called():
        raise AssertionError("network should not be attempted in offline mode")

    with pytest.raises(OfflineCacheMissError) as exc_info:
        _cached("missing-key", fail_if_called, offline=True)
    msg = str(exc_info.value)
    assert "--offline" in msg
    assert "missing-key" in msg


def test_network_failure_raises_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))

    def boom():
        raise ConnectionError("timed out")

    with pytest.raises(RuntimeError) as exc_info:
        _cached("k", boom)
    msg = str(exc_info.value)
    assert "datacenter" in msg.lower()
    assert "data/cache" in msg or "cache" in msg.lower()
    assert "--offline" in msg


def test_get_season_boxscores_offline_missing_season_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("ingest.nba_boxscores.CACHE_DIR", str(tmp_path))
    with pytest.raises(OfflineCacheMissError):
        get_season_boxscores("1999-00", offline=True)
