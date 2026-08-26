"""
ingest/nba_rosters.py -- C1: player positions (and current team) via CommonTeamRoster.

LeagueDashPlayerStats (ingest/nba_boxscores.py) carries team but not position --
verified against the committed cache, no POSITION column anywhere in its headers.
Position isn't available from any single-call, whole-league endpoint, so this
pulls CommonTeamRoster once per team (30 teams -> 30 calls per season) and joins
by nba_id, same disk-cache pattern as nba_boxscores.py (one cache file per call,
reused across runs).

Two seasons are pulled and merged: the upcoming season (current, post-trade
affiliation -- what matters for a 2026-27 draft board) and the most recently
completed one (fallback, for players not yet on a finalized upcoming roster,
or dropped off a roster entirely). This far before opening night the upcoming
season's rosters may not be fully populated yet; get_current_rosters() reports
coverage for both seasons so that's visible rather than silently swallowed.

Positions from this endpoint are coarse -- "Guard"/"Forward"/"Center" and
hyphenated combos like "Guard-Forward" (older responses use abbreviated forms
like "G", "F", "G-F") -- there is no PG/SG or SF/PF split anywhere in nba_api.
Everything downstream (RosterSlot eligibility in engine/value.py) is built
around that ceiling: a "G" eligibility group covers PG/SG roster slots, "F"
covers SF/PF.

Run from the repo root:
    python ingest/nba_rosters.py            # pulls upcoming + fallback season rosters, caches, prints coverage
    python ingest/nba_rosters.py --offline  # cache-only: fails fast instead of hitting the network
    python ingest/nba_rosters.py --selftest # offline check of parsing + normalization + merge, no network
"""

from __future__ import annotations

import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingest.nba_boxscores import OfflineCacheMissError, _cached  # noqa: E402


def normalize_position(raw: str | None) -> tuple[str, ...]:
    """Raw CommonTeamRoster POSITION string -> sorted tuple of coarse eligibility
    groups, e.g. ('G',) or ('F', 'G') for a "Guard-Forward" combo. Matches both
    the spelled-out and abbreviated forms nba_api has used, case-insensitively.
    Unknown/missing input returns an empty tuple, not a guess."""
    if not raw:
        return ()
    text = raw.strip().lower()
    groups = set()
    if "guard" in text:
        groups.add("G")
    if "forward" in text:
        groups.add("F")
    if "center" in text:
        groups.add("C")
    if not groups:
        for token in re.split(r"[^a-z]+", text):
            if token in ("g", "f", "c"):
                groups.add(token.upper())
    return tuple(sorted(groups))


def _team_list() -> list[tuple[int, str]]:
    from nba_api.stats.static import teams  # lazy import; bundled static data, no network

    return [(t["id"], t["abbreviation"]) for t in teams.get_teams()]


def _fetch_team_roster(team_id: int, season: str) -> dict:
    from nba_api.stats.endpoints import commonteamroster  # lazy import

    resp = commonteamroster.CommonTeamRoster(team_id=team_id, season=season, timeout=60)
    return resp.get_dict()


def _roster_result_set(raw: dict) -> dict:
    """CommonTeamRoster's response has multiple resultSets (Coaches, CommonTeamRoster);
    find by name rather than assume an index, since Coaches isn't always present."""
    for rs in raw.get("resultSets", []):
        if rs.get("name") == "CommonTeamRoster":
            return rs
    raise ValueError("CommonTeamRoster result set not found in response")


def _rows_to_roster(raw: dict, team_abbr: str) -> dict[int, dict]:
    rs = _roster_result_set(raw)
    col_index = {h: i for i, h in enumerate(rs["headers"])}
    out: dict[int, dict] = {}
    for row in rs["rowSet"]:
        pid = row[col_index["PLAYER_ID"]]
        pos_raw = row[col_index["POSITION"]] if "POSITION" in col_index else None
        out[pid] = {"position": normalize_position(pos_raw), "team": team_abbr}
    return out


def get_season_rosters(season: str, force: bool = False, offline: bool = False) -> tuple[dict[int, dict], dict]:
    """Every player's position + team for `season`, keyed by nba_id, via one
    CommonTeamRoster call per team (30 calls, individually cached). Returns
    (players, stats); stats reports how many of the 30 teams actually had a
    roster on file, so a season that isn't populated yet (or a partial cache)
    is visible rather than silently thin."""
    players: dict[int, dict] = {}
    teams_with_data = 0
    team_list = _team_list()
    for i, (team_id, abbr) in enumerate(team_list):
        key = f"commonteamroster_{season}_{team_id}"
        raw, src = _cached(key, lambda tid=team_id: _fetch_team_roster(tid, season),
                           force=force, offline=offline)
        roster = _rows_to_roster(raw, abbr)
        if roster:
            teams_with_data += 1
        players.update(roster)
        if src == "network" and i < len(team_list) - 1:
            time.sleep(1)  # be polite to stats.nba.com between live pulls
    stats = {"season": season, "teams_total": len(team_list), "teams_with_data": teams_with_data}
    return players, stats


def _next_season(latest: str) -> str:
    start = int(latest.split("-")[0]) + 1
    return f"{start}-{str(start + 1)[2:]}"


def get_current_rosters(force: bool = False, offline: bool = False) -> tuple[dict[int, dict], dict]:
    """Position + team for every currently relevant player, merged across the
    upcoming season's rosters (preferred -- reflects offseason trades) and the
    most recently completed season's (fallback, for players not yet on a
    finalized upcoming roster). Returns (players, stats) with coverage for
    both seasons so a mostly-empty upcoming pull is visible, not silent."""
    from ingest.nba_boxscores import recent_completed_seasons

    latest_completed = recent_completed_seasons(1)[0]
    upcoming = _next_season(latest_completed)

    fallback, fallback_stats = get_season_rosters(latest_completed, force=force, offline=offline)
    current, current_stats = get_season_rosters(upcoming, force=force, offline=offline)

    merged = dict(fallback)
    merged.update(current)  # upcoming-season roster wins where it has data

    stats = {
        "upcoming_season": upcoming, "upcoming": current_stats,
        "fallback_season": latest_completed, "fallback": fallback_stats,
        "merged_players": len(merged),
    }
    return merged, stats


def team_changes(force: bool = False, offline: bool = False) -> dict[int, tuple[str, str]]:
    """{nba_id: (old_team, new_team)} for every player on a roster in BOTH the
    most recently completed season and the upcoming one whose team abbreviation
    differs -- the offseason movement _role_trend_mult (engine/projection.py)
    can't see. Players present in only one season (incoming rookies, players who
    left the league) are omitted: there's no before/after to compare. Reads the
    same per-team CommonTeamRoster cache as get_season_rosters -- diagnostic
    only, it drives no automatic adjustment (that stays in data/role_overrides.json,
    hand-edited)."""
    from ingest.nba_boxscores import recent_completed_seasons

    latest_completed = recent_completed_seasons(1)[0]
    upcoming = _next_season(latest_completed)
    old, _ = get_season_rosters(latest_completed, force=force, offline=offline)
    new, _ = get_season_rosters(upcoming, force=force, offline=offline)

    out: dict[int, tuple[str, str]] = {}
    for pid, r in new.items():
        o = old.get(pid)
        if o and o.get("team") and r.get("team") and o["team"] != r["team"]:
            out[pid] = (o["team"], r["team"])
    return out


def load_rosters_or_warn(quiet: bool = False) -> dict[int, dict]:
    """Best-effort position/team lookup, cache-only (offline=True): a missing
    roster cache degrades to an empty map (the flat replacement handles unknown
    positions -- see engine/value.py) rather than the caller attempting 60 live
    network calls that hang from a datacenter IP. Run `python ingest/nba_rosters.py`
    locally to populate the cache (see README.md); until then, callers just run
    without positions. Shared by cli/board.py and backtest/validate.py so the
    fallback behavior (and its message) only lives in one place."""
    try:
        rosters, _stats = get_current_rosters(offline=True)
        return rosters
    except OfflineCacheMissError:
        if not quiet:
            print("Note: no cached position/team data -- run `python ingest/nba_rosters.py` "
                  "locally (see README.md) to add it. Continuing without positions.",
                  file=sys.stderr)
        return {}


def attach_positions(players: dict[int, dict], rosters: dict[int, dict] | None = None,
                     force: bool = False, offline: bool = False) -> dict[int, dict]:
    """Return a copy of `players` with 'position' (tuple of eligibility groups,
    e.g. ('G',) or ('F', 'G')) and 'team' (current, post-trade abbreviation
    where known) attached from get_current_rosters(). Pass `rosters` to reuse
    an already-fetched roster map instead of pulling it again per call site.

    Players with no roster match anywhere (retired, overseas, unsigned) get
    position=() -- an empty eligibility set. engine/value.py's replacement
    logic treats that as unknown and falls back to the flat replacement,
    rather than excluding the player from the board."""
    if rosters is None:
        rosters, _stats = get_current_rosters(force=force, offline=offline)
    out = {}
    for pid, p in players.items():
        q = dict(p)
        r = rosters.get(pid)
        q["position"] = r["position"] if r else ()
        if r:
            q["team"] = r["team"]
        out[pid] = q
    return out


def _demo(offline: bool = False) -> None:
    from collections import Counter

    from util.console import configure_stdout_utf8
    configure_stdout_utf8()

    rosters, stats = get_current_rosters(offline=offline)
    up, fb = stats["upcoming"], stats["fallback"]
    print(f"Upcoming season {stats['upcoming_season']}: "
          f"{up['teams_with_data']}/{up['teams_total']} teams have a roster on file")
    print(f"Fallback season {stats['fallback_season']}: "
          f"{fb['teams_with_data']}/{fb['teams_total']} teams have a roster on file")
    print(f"{stats['merged_players']} players with a known position/team\n")

    counts = Counter(rosters[pid]["position"] for pid in rosters)
    for group, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        label = "/".join(group) if group else "(unknown)"
        print(f"   {label:<8} {n}")


def _selftest() -> None:
    """Thin wrapper: runs tests/test_nba_rosters.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_nba_rosters.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("selftest ok: see tests/test_nba_rosters.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo(offline="--offline" in sys.argv)
