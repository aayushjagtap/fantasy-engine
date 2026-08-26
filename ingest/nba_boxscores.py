"""
ingest/nba_boxscores.py -- M1 ingestion.

Pull season box-score lines for every player from nba_api's LeagueDashPlayerStats
(one call = every player's season line), cache the raw response to disk, and return
a dict keyed by nba_id -- the canonical key from the DARKO decision (M0.5).

Why LeagueDashPlayerStats: one network call yields the full-league season line
(pts, reb, ast, stl, blk, tov, threes, and the shooting splits), which is exactly
the raw material the value engine and the projection baseline need. One call per
season means minimal rate-limit exposure, and the cache means you pay that cost
once.

Run from the repo root:
    python ingest/nba_boxscores.py            # pulls the recent completed seasons, caches, prints a sanity check
    python ingest/nba_boxscores.py --offline  # cache-only: fails fast instead of hitting the network
    python ingest/nba_boxscores.py --selftest # offline check of parsing + caching + season logic, no network

Cached responses land in data/cache/ (committed to the repo -- see README.md).
Delete a file there to force a re-pull, or call with force=True.

NOTE: stats.nba.com silently drops connections from datacenter IPs (AWS/GCP/
Azure), so a live pull from a cloud environment (e.g. GitHub Codespaces) will
hang or return empty rather than fail cleanly. Pull on a local/home connection
and commit the cache; use --offline anywhere the data should already be cached
so a missing key fails fast instead of hanging. See README.md.
"""

from __future__ import annotations

import datetime
import json
import os
import time

CACHE_DIR = "data/cache"

# nba_api LeagueDashPlayerStats column -> our field name. nba_id first, since it's
# the canonical join key (matches DARKO's nba_id and the Sleeper crosswalk target).
FIELD_MAP = {
    "PLAYER_ID": "nba_id",
    "PLAYER_NAME": "name",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team",
    "AGE": "age",
    "GP": "gp",
    "MIN": "min",
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TOV": "tov",
    "FG3M": "fg3m",
    "FGM": "fgm",
    "FGA": "fga",
    "FG_PCT": "fg_pct",
    "FTM": "ftm",
    "FTA": "fta",
    "FT_PCT": "ft_pct",
}


def recent_completed_seasons(n: int = 4, today: datetime.date | None = None) -> list[str]:
    """
    The n most recently COMPLETED NBA seasons as 'YYYY-YY' strings, newest first.

    A season labeled 'A-B' (B = A+1) runs ~Oct of year A to Jun of year B. We treat
    it as complete once we're at/after July 1 of year B (i.e. into the offseason).
    Deriving this from the date means the default never goes stale.
    """
    today = today or datetime.date.today()
    latest_end_year = today.year if today >= datetime.date(today.year, 7, 1) else today.year - 1
    return [f"{end - 1}-{str(end)[2:]}" for end in range(latest_end_year, latest_end_year - n, -1)]


def _cache_path(key: str) -> str:
    safe = key.replace("/", "_").replace(" ", "_")
    return os.path.join(CACHE_DIR, safe + ".json")


class OfflineCacheMissError(RuntimeError):
    """Raised in --offline mode when a requested key has no cached data."""


def _offline_miss_message(key: str) -> str:
    path = _cache_path(key)
    return (
        f"--offline was set but no cached data exists for {key!r} "
        f"(looked for {path}).\n\n"
        "Cache-only mode fails fast instead of hanging or hitting the network. "
        "To fix this, pull the missing season on a local machine (home IP, not "
        "a datacenter/cloud IP) with `python ingest/nba_boxscores.py`, then "
        f"commit the resulting file(s) under {CACHE_DIR}/ so this environment "
        "can read them offline. See README.md for the full workflow."
    )


def _network_failure_message(key: str, err: Exception) -> str:
    path = _cache_path(key)
    return (
        f"Live pull for {key!r} failed: {err!r}\n\n"
        "stats.nba.com sits behind Akamai bot protection and silently drops "
        "connections from datacenter IPs (AWS/GCP/Azure) -- from a cloud "
        "environment (e.g. GitHub Codespaces) this typically shows up as a "
        "hang or an empty response, not a clean error.\n\n"
        "Fix: pull this season's data on a local machine (home IP) with "
        "`python ingest/nba_boxscores.py`, then commit the resulting file "
        f"({path}) under {CACHE_DIR}/ so this environment can read it "
        "offline -- see README.md. To fail fast instead of hanging on the "
        "next attempt, pass --offline (or offline=True) to force cache-only "
        "lookups."
    )


def _cached(key: str, fetch_fn, force: bool = False, offline: bool = False):
    """Return (data, source) where source is 'cache' or 'network'. Caches to disk."""
    path = _cache_path(key)
    if not force and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), "cache"
    if offline:
        raise OfflineCacheMissError(_offline_miss_message(key))
    try:
        raw = fetch_fn()
    except Exception as e:
        raise RuntimeError(_network_failure_message(key, e)) from e
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return raw, "network"


def _fetch_league_dash(season: str, per_mode: str, season_type: str) -> dict:
    from nba_api.stats.endpoints import leaguedashplayerstats  # lazy import

    resp = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        per_mode_detailed=per_mode,
        season_type_all_star=season_type,
        timeout=60,
    )
    return resp.get_dict()


def _rows_to_players(raw: dict) -> dict[int, dict]:
    result_set = raw["resultSets"][0]
    col_index = {h: i for i, h in enumerate(result_set["headers"])}
    players: dict[int, dict] = {}
    for row in result_set["rowSet"]:
        rec = {key: row[col_index[col]] for col, key in FIELD_MAP.items() if col in col_index}
        players[rec["nba_id"]] = rec
    return players


def get_season_boxscores(
    season: str,
    per_mode: str = "PerGame",
    season_type: str = "Regular Season",
    force: bool = False,
    offline: bool = False,
) -> tuple[dict[int, dict], str]:
    """Every player's season box-score line for `season`, keyed by nba_id.

    offline=True forces cache-only lookups and fails fast (OfflineCacheMissError)
    if the season isn't already cached, instead of attempting a network pull that
    will hang or return empty from a datacenter IP.
    """
    key = f"leaguedashplayerstats_{season}_{per_mode}_{season_type}"
    raw, src = _cached(key, lambda: _fetch_league_dash(season, per_mode, season_type),
                       force=force, offline=offline)
    return _rows_to_players(raw), src


def _demo(offline: bool = False) -> None:
    import sys as _sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from util.console import configure_stdout_utf8
    configure_stdout_utf8()

    seasons = recent_completed_seasons(4)
    print(f"Recent completed seasons: {', '.join(seasons)}\n")
    for season in seasons:
        players, src = get_season_boxscores(season, offline=offline)
        print(f"{season}: {len(players)} players ({src})")
        top = max(players.values(), key=lambda p: p.get("pts") or 0)
        print(
            f"   top scorer: {top['name']} -- "
            f"{top['pts']} pts / {top['reb']} reb / {top['ast']} ast "
            f"({top['gp']} GP, {top['min']} min)"
        )
        if src == "network":
            time.sleep(1)  # be polite to stats.nba.com between live pulls
    print("\nCached to data/cache/ -- reruns are instant and hit no network.")


def _selftest() -> None:
    """Thin wrapper: runs tests/test_nba_boxscores.py under pytest.

    The real assertions live in tests/ (pytest-discoverable, CI-runnable);
    this just gives `python ingest/nba_boxscores.py --selftest` a quick,
    no-typing-the-path way to run them.
    """
    import pytest

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc = pytest.main(["-q", os.path.join(root, "tests", "test_nba_boxscores.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("selftest ok: see tests/test_nba_boxscores.py")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo(offline="--offline" in sys.argv)
