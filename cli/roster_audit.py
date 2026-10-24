"""Print an offline audit of cached NBA roster and season-stat coverage."""
from __future__ import annotations

import sys

from ingest.nba_boxscores import OfflineCacheMissError
from ingest.roster_audit import load_roster_audit


def _print_players(title: str, players: list[dict], *, history: bool = False) -> None:
    print(f"\n{title} ({len(players)})")
    if not players:
        print("  (none)")
        return
    for player in players:
        name = player.get("name") or "(name unavailable)"
        position = "/".join(player.get("position") or ()) or "?"
        row = f"  {name} | NBA ID {player['nba_id']} | {player.get('team') or '?'} | {position}"
        if history:
            seasons = ", ".join(player.get("history_seasons", [])) or "none"
            row += f" | older cached history: {seasons}"
        print(row)


def _print_coverage(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    for row in rows:
        state = f"{row['player_count']} players" if row["has_data"] else "no roster response"
        print(f"  {row['team']}: {state}")


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("Error: this audit is cache-only and takes no options", file=sys.stderr)
        return 2
    try:
        report = load_roster_audit()
    except (OfflineCacheMissError, ValueError, KeyError) as e:
        print(f"Error: cache-only roster audit could not complete: {e}", file=sys.stderr)
        return 1

    print("NBA ROSTER AND PLAYER COVERAGE AUDIT")
    print("Source: cached roster and season-stat responses only; no live requests.")
    print("Counts and findings below are observations from these cache files, not claims about current NBA status.")
    print(f"Upcoming roster season: {report['upcoming_season']} | latest completed stats season: "
          f"{report['latest_season']}")
    print(f"Current-only roster: {report['current_count']} players | fallback roster: "
          f"{report['fallback_count']} players | merged fallback+current: {report['merged_count']} players")

    _print_coverage(f"Upcoming season {report['upcoming_season']} team coverage",
                    report["current_coverage"])
    _print_coverage(f"Fallback season {report['latest_season']} team coverage",
                    report["fallback_coverage"])
    _print_players("Current-roster players with missing/unknown coarse position", report["missing_positions"])
    _print_players("Fallback-roster players with missing/unknown coarse position",
                   report["fallback_missing_positions"])
    _print_players("Fallback-only players (not current-roster members)", report["fallback_only"])
    _print_players(f"Current-roster players without {report['latest_season']} stats, with older history",
                   report["with_older_history"], history=True)
    _print_players(f"Current-roster players without {report['latest_season']} stats or any cached history",
                   report["without_history"])
    print(f"\nPotential roster/stat name-to-ID mismatches ({len(report['mismatches'])})")
    if not report["mismatches"]:
        print("  (none detected)")
    for item in report["mismatches"]:
        if item.get("kind"):
            print(f"  {item['kind']} | name: {item.get('roster_name') or '?'} | "
                  f"NBA IDs: {', '.join(map(str, item['nba_ids']))} | team(s): {item['team']}")
        else:
            position = "/".join(item.get("position") or ()) or "?"
            print(f"  NBA ID {item['nba_id']} | team {item.get('team') or '?'} | {position} | "
                  f"roster name: {item['roster_name']} | {item['season']} stats name: {item['stats_name']}")

    print("\nPotential duplicate identities in roster responses")
    duplicates = report["duplicates"]
    if not duplicates:
        print("  (none detected)")
    for item in duplicates:
        position = "/".join(item.get("position") or ()) or "?"
        print(f"  NBA ID {item['nba_id']} | {item.get('name') or '(name unavailable)'} | "
              f"teams: {', '.join(item['teams'])} | position: {position} | season: {item['season']}")

    print("\nPreviously flagged player names (matched by normalized name against current cached roster)")
    for player in report["watchlist"]:
        if player["nba_id"] is None:
            ids = player.get("ambiguous_ids", [])
            if ids:
                print(f"  {player['requested_name']}: ambiguous current roster name; NBA IDs: "
                      f"{', '.join(map(str, ids))}")
            else:
                print(f"  {player['requested_name']}: no matching identity found in current cached roster")
        else:
            position = "/".join(player["position"]) or "?"
            print(f"  {player['name']} | NBA ID {player['nba_id']} | {player['team'] or '?'} | {position} | "
                  f"missing latest stats={player['missing_latest_stats']} | "
                  f"older history={player['older_history']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
