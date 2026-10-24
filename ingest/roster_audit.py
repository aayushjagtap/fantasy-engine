"""Cache-only audit of current NBA roster and season-stat coverage."""
from __future__ import annotations

import os
import re

from crosswalk.names import normalize_name
from ingest import nba_boxscores, nba_rosters
from ingest.nba_boxscores import OfflineCacheMissError


WATCHLIST = ("Tyrese Haliburton", "Damian Lillard", "Kyrie Irving",
             "Fred VanVleet", "Trey Lyles", "Adam Flagler")


def cached_stat_seasons(cache_dir: str | None = None) -> list[str]:
    pattern = re.compile(r"leaguedashplayerstats_(\d{4}-\d{2})_PerGame_Regular_Season\.json$")
    seasons = set()
    cache_dir = cache_dir or nba_boxscores.CACHE_DIR
    if os.path.isdir(cache_dir):
        for filename in os.listdir(cache_dir):
            match = pattern.fullmatch(filename)
            if match:
                seasons.add(match.group(1))
    return sorted(seasons, key=lambda season: int(season[:4]), reverse=True)


def build_audit(current: dict[int, dict], fallback: dict[int, dict],
                current_coverage: list[dict], fallback_coverage: list[dict],
                latest_stats: dict[int, dict], history: dict[str, dict[int, dict]],
                latest_season: str, upcoming_season: str,
                duplicate_ids: list[dict] | None = None) -> dict:
    merged = dict(fallback)
    merged.update(current)
    missing_latest = sorted(set(current) - set(latest_stats))
    older_ids = set().union(*(set(rows) for season, rows in history.items()
                              if season != latest_season)) if history else set()

    mismatches = []
    for pid, roster in current.items():
        for season, rows in history.items():
            stat_name = rows.get(pid, {}).get("name")
            roster_name = roster.get("name")
            if stat_name and roster_name and normalize_name(stat_name) != normalize_name(roster_name):
                mismatches.append({"nba_id": pid, "roster_name": roster_name,
                                   "stats_name": stat_name, "season": season,
                                   "team": roster.get("team"),
                                   "position": roster.get("position", ())})
    mismatches = list({(r["nba_id"], r["stats_name"], r["season"]): r for r in mismatches}.values())
    ids_by_name: dict[str, list[tuple[int, dict]]] = {}
    for pid, player in current.items():
        key = normalize_name(player.get("name") or "")
        if key:
            ids_by_name.setdefault(key, []).append((pid, player))
    for matches in ids_by_name.values():
        if len({pid for pid, _ in matches}) > 1:
            mismatches.append({"kind": "same normalized roster name has multiple NBA IDs",
                               "nba_ids": [pid for pid, _ in matches],
                               "roster_name": matches[0][1].get("name"),
                               "stats_name": "", "season": upcoming_season,
                               "team": ", ".join(p.get("team", "?") for _, p in matches),
                               "position": ()})

    missing_positions = [{"nba_id": pid, **current[pid]} for pid in sorted(current)
                         if not current[pid].get("position")]
    fallback_missing_positions = [{"nba_id": pid, **fallback[pid]} for pid in sorted(fallback)
                                  if not fallback[pid].get("position")]
    fallback_only = [{"nba_id": pid, **fallback[pid]} for pid in sorted(set(fallback) - set(current))]
    missing_rows = []
    for pid in missing_latest:
        player = current[pid]
        missing_rows.append({"nba_id": pid, "name": player.get("name"),
                             "team": player.get("team"), "position": player.get("position", ()),
                             "older_history": pid in older_ids,
                             "history_seasons": [s for s, rows in history.items()
                                                 if s != latest_season and pid in rows]})

    watch = []
    by_name: dict[str, list[tuple[int, dict]]] = {}
    for pid, player in current.items():
        key = normalize_name(player.get("name") or "")
        if key:
            by_name.setdefault(key, []).append((pid, player))
    for name in WATCHLIST:
        matches = by_name.get(normalize_name(name), [])
        if len(matches) == 1:
            found = matches[0]
            pid, player = found
            watch.append({"requested_name": name, "nba_id": pid, "name": player.get("name"),
                          "team": player.get("team"), "position": player.get("position", ()),
                          "missing_latest_stats": pid not in latest_stats,
                          "older_history": pid in older_ids})
        else:
            watch.append({"requested_name": name, "nba_id": None, "name": None,
                          "team": None, "position": (), "missing_latest_stats": None,
                          "older_history": None, "ambiguous_ids": [pid for pid, _ in matches]})

    return {
        "latest_season": latest_season, "upcoming_season": upcoming_season,
        "current_count": len(current), "fallback_count": len(fallback),
        "merged_count": len(merged), "current_coverage": current_coverage,
        "fallback_coverage": fallback_coverage, "missing_positions": missing_positions,
        "fallback_missing_positions": fallback_missing_positions,
        "fallback_only": fallback_only, "missing_latest": missing_rows,
        "without_history": [r for r in missing_rows if not r["older_history"]],
        "with_older_history": [r for r in missing_rows if r["older_history"]],
        "mismatches": mismatches, "watchlist": watch,
        "duplicates": duplicate_ids or [],
    }


def load_roster_audit() -> dict:
    latest = nba_boxscores.recent_completed_seasons(1)[0]
    upcoming = nba_rosters._next_season(latest)
    fallback, fallback_coverage, fallback_duplicates = _cached_season_rosters(latest)
    current, current_coverage, current_duplicates = _cached_season_rosters(upcoming)
    cached_seasons = cached_stat_seasons()
    history = {season: nba_boxscores.get_season_boxscores(season, offline=True)[0]
               for season in cached_seasons}
    latest_stats = history.get(latest)
    if latest_stats is None:
        latest_stats = nba_boxscores.get_season_boxscores(latest, offline=True)[0]
        history[latest] = latest_stats
    duplicates = ([{"season": upcoming, **item} for item in current_duplicates]
                  + [{"season": latest, **item} for item in fallback_duplicates])
    return build_audit(current, fallback, current_coverage, fallback_coverage,
                       latest_stats, history, latest, upcoming, duplicates)


def _cached_season_rosters(season: str) -> tuple[dict[int, dict], list[dict], list[dict]]:
    players = {}
    coverage = []
    player_teams = {}
    duplicates = []
    for team_id, abbr in nba_rosters._team_list():
        key = f"commonteamroster_{season}_{team_id}"
        try:
            raw, _ = nba_boxscores._cached(key, lambda: None, offline=True)
            roster = nba_rosters._rows_to_roster(raw, abbr)
        except OfflineCacheMissError:
            roster = None
        coverage.append({"team_id": team_id, "team": abbr, "has_data": bool(roster),
                         "player_count": len(roster or {})})
        for pid, player in (roster or {}).items():
            if pid in player_teams and player_teams[pid] != abbr:
                duplicates.append({"nba_id": pid, "name": player.get("name"),
                                   "teams": [player_teams[pid], abbr],
                                   "position": player.get("position", ())})
            else:
                player_teams[pid] = abbr
        players.update(roster or {})
    return players, coverage, duplicates
