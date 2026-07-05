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
    python ingest/nba_boxscores.py --selftest # offline check of parsing + caching + season logic, no network

Cached responses land in data/cache/ (gitignored). Delete a file there to force a
re-pull, or call with force=True.
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


def _cached(key: str, fetch_fn, force: bool = False):
    """Return (data, source) where source is 'cache' or 'network'. Caches to disk."""
    path = _cache_path(key)
    if not force and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), "cache"
    raw = fetch_fn()
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
) -> tuple[dict[int, dict], str]:
    """Every player's season box-score line for `season`, keyed by nba_id."""
    key = f"leaguedashplayerstats_{season}_{per_mode}_{season_type}"
    raw, src = _cached(key, lambda: _fetch_league_dash(season, per_mode, season_type), force=force)
    return _rows_to_players(raw), src


def _demo() -> None:
    seasons = recent_completed_seasons(4)
    print(f"Recent completed seasons: {', '.join(seasons)}\n")
    for season in seasons:
        players, src = get_season_boxscores(season)
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
    # 1) parsing: a fake response in nba_api's shape must key cleanly by nba_id.
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

    # 2) season logic: derived seasons must be correct and date-driven.
    assert recent_completed_seasons(3, datetime.date(2026, 7, 4)) == ["2025-26", "2024-25", "2023-24"]
    # Mid-season (Jan) -> current season not yet complete, so latest is the prior one.
    assert recent_completed_seasons(2, datetime.date(2026, 1, 15)) == ["2024-25", "2023-24"]

    # 3) caching: fetch_fn must run once, then be served from disk.
    import tempfile
    globals()["CACHE_DIR"] = tempfile.mkdtemp()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"payload": calls["n"]}

    r1, s1 = _cached("k", fetch)
    r2, s2 = _cached("k", fetch)
    assert (s1, s2) == ("network", "cache"), (s1, s2)
    assert calls["n"] == 1, "cache should prevent a second fetch"
    assert r1 == r2

    print("selftest ok: parsing keys by nba_id, season logic date-driven, cache serves from disk")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
