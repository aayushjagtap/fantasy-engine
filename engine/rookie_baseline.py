"""Offline historical young-player baselines for the 2026-27 rookie class."""
from __future__ import annotations

import glob
import json
import os
import statistics

from ingest.nba_boxscores import _rows_to_players
from ingest.nba_rosters import normalize_position

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = os.path.join(_ROOT, "data", "cache")
MIN_OBSERVATIONS = 20
MIN_AGE = 20
MIN_GAMES = 40
COMPONENTS = ("gp", "min", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov")
ROLE_COMPONENTS = tuple(c for c in COMPONENTS if c != "gp")
OPPORTUNITY_MULTIPLIERS = {
    "Darryn Peterson": 1.30,
    "AJ Dybantsa": 1.15,
    "Cameron Boozer": 1.00,
    "Aday Mara": 0.70,
    "Jayden Quaintance": 0.70,
    "Chris Cenac Jr.": 0.70,
}
SEASON_FILES = tuple(sorted(glob.glob(os.path.join(
    _CACHE, "leaguedashplayerstats_*_PerGame_Regular_Season.json"))))


def primary_group(position) -> str | None:
    """Map multi-positions once by fixed C > F > G precedence."""
    groups = normalize_position("-".join(position) if isinstance(position, (list, tuple)) else position)
    return next((g for g in ("C", "F", "G") if g in groups), None)


def summarize_comparators(season_lines: dict[str, dict], roster_positions: dict[int, tuple],
                          min_observations: int = MIN_OBSERVATIONS) -> dict:
    """Median observed per-game components from eligible, unique player-seasons."""
    observations = {g: {} for g in ("G", "F", "C")}
    for season, players in season_lines.items():
        for pid, line in players.items():
            age, gp = line.get("age"), line.get("gp")
            if age is None or age > MIN_AGE or gp is None or gp < MIN_GAMES:
                continue
            group = primary_group(roster_positions.get(pid, ()))
            if not group or any(line.get(k) is None for k in COMPONENTS):
                continue
            observations[group][(season, int(pid))] = {k: float(line[k]) for k in COMPONENTS}
    out = {}
    for group, keyed in observations.items():
        values = list(keyed.values())
        out[group] = {
            "sample_size": len(values), "supported": len(values) >= min_observations,
            "components": ({k: statistics.median(row[k] for row in values) for k in COMPONENTS}
                           if len(values) >= min_observations else None),
            "criteria": f"age <= {MIN_AGE}; GP >= {MIN_GAMES}; regular-season PerGame; unique player-season",
        }
    return out


def load_comparator_baselines() -> tuple[dict, dict]:
    """Read only committed NBA caches; positions are a 2026-27 roster proxy."""
    season_lines = {}
    for path in SEASON_FILES:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        season = os.path.basename(path).split("_")[1]
        season_lines[season] = _rows_to_players(raw)
    positions = {}
    for path in glob.glob(os.path.join(_CACHE, "commonteamroster_2026-27_*.json")):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for rs in raw.get("resultSets", []):
            if rs.get("name") != "CommonTeamRoster":
                continue
            ix = {name: i for i, name in enumerate(rs["headers"])}
            for row in rs["rowSet"]:
                positions[int(row[ix["PLAYER_ID"]])] = normalize_position(row[ix["POSITION"]])
    return summarize_comparators(season_lines, positions), {
        "seasons": [os.path.basename(p).split("_")[1] for p in SEASON_FILES],
        "roster_proxy": "CommonTeamRoster 2026-27",
    }


def make_rookie_line(entry: dict, baseline: dict | None) -> dict | None:
    """Construct a conservative line, or None when its position lacks support."""
    if not baseline or not baseline.get("supported"):
        return None
    group = primary_group(entry.get("position", ()))
    if not group or group != entry.get("baseline_position", group):
        return None
    mult = float(OPPORTUNITY_MULTIPLIERS.get(entry["name"], 1.0))
    line = dict(baseline["components"])
    for comp in ROLE_COMPONENTS:
        line[comp] *= mult
    line.update({
        "name": entry["name"], "age": entry.get("age"), "position": tuple(entry["position"]),
        "team": entry.get("team"), "role_mult": mult, "is_rookie": True,
        "rookie_season": "2026-27",
        "projection_source": "historical_young_player_baseline",
        "projection_is_estimate": True,
        "comparator_criteria": baseline["criteria"],
        "comparator_sample_size": baseline["sample_size"],
        "comparator_position": group, "draft_pick": entry["draft_pick"],
        "manual_opportunity_multiplier": mult if entry["name"] in OPPORTUNITY_MULTIPLIERS else None,
    })
    line["pts"] = 2 * line["fgm"] + line["fg3m"] + line["ftm"]
    line["fg_pct"] = line["fgm"] / line["fga"] if line["fga"] else 0.0
    line["ft_pct"] = line["ftm"] / line["fta"] if line["fta"] else 0.0
    return line
