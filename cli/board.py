"""
cli/board.py -- M4, the front door to the value engine.

Prints (and optionally exports) a ranked draft board from a league config,
explains a single player's value breakdown, or compares the same player pool
across two league configs to see who moves.

Run from the repo root:
    python -m cli.board --league leagues/standard_9cat.json --top 150 --csv out/board.csv
    python -m cli.board --league leagues/punt_ft.json --basis per_game
    python -m cli.board --explain "Victor Wembanyama" --league leagues/standard_9cat.json
    python -m cli.board --compare leagues/standard_9cat.json leagues/punt_ft.json

--top controls the printed table only. The board always ranks and exports
every eligible player -- printing the top 50 while exporting the full pool
to a spreadsheet is the common case, and re-running with a bigger --top
just to get a complete CSV would be a bad default.

engine/ stays surface-agnostic: this module imports FROM engine/, never
the other way around.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pydantic

from engine.diagnose import explain
from engine.league_config import LeagueConfig
from engine.value import REPLACEMENT_MODES, _rank_movers, compute_values


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cli.board",
        description="Ranked draft board, player explain, and cross-league comparison.",
    )
    p.add_argument("--league", help="path to a league config (.json/.yaml); required unless --compare")
    p.add_argument("--top", type=int, default=150, help="rows to print (default 150); does not limit --csv")
    p.add_argument("--csv", metavar="PATH", help="write the full board to PATH")
    p.add_argument("--basis", choices=("availability_adjusted", "per_game", "total"),
                   default="availability_adjusted")
    p.add_argument("--replacement", choices=REPLACEMENT_MODES, default="flat",
                   help="replacement-level baseline for VOR (default flat, the current/recorded-backtest "
                        "behavior); 'hybrid'/'strict' need position data -- see ingest/nba_rosters.py")
    p.add_argument("--explain", metavar="NAME", help="print a per-category breakdown for one player")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"), help="two league configs to diff movers between")
    p.add_argument("--season", help="season anchoring the 3-season projection window (default: latest 3 completed)")
    p.add_argument("--actuals", action="store_true", help="use one season's raw box scores instead of projecting")
    return p


def _normalize_basis(basis: str) -> str:
    """'total' is an accepted alias engine-side, but the CLI only ever wants to
    see/print the current name -- normalize once so it never leaks back out."""
    return "availability_adjusted" if basis == "total" else basis


def _projection_window(season: str | None, n: int = 3) -> list[str]:
    """n seasons ending at `season` inclusive, newest first. None -> latest n completed."""
    from ingest.nba_boxscores import recent_completed_seasons

    if season is None:
        return recent_completed_seasons(n)
    start = int(season.split("-")[0])
    return [f"{y}-{str(y + 1)[2:]}" for y in range(start, start - n, -1)]


def _load_players(args: argparse.Namespace) -> tuple[dict, str]:
    """(players, description). Only reads args.actuals/args.season -- well-defined
    whether or not --league was given, so it can run before the --league check."""
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from ingest.nba_rosters import attach_positions, load_rosters_or_warn

    rosters = load_rosters_or_warn()

    if args.actuals:
        season = args.season or recent_completed_seasons(1)[0]
        players, _src = get_season_boxscores(season)
        return attach_positions(players, rosters=rosters), f"actuals: {season}"

    from engine.projection import _next_season_label, project_players

    seasons = _projection_window(args.season)
    lines = [get_season_boxscores(s)[0] for s in seasons]
    # project_players only reads position/team off the newest season a player
    # appears in (history[0], always seasons[0] for players it projects) --
    # attaching to the rest would just be repeat work on data never read.
    lines[0] = attach_positions(lines[0], rosters=rosters)
    projected, _aged = project_players(lines)
    upcoming = _next_season_label(seasons[0])
    return projected, f"projected {upcoming} from {', '.join(seasons)}"


def _board_rows(players: dict, cfg: LeagueConfig, basis: str, replacement_mode: str = "flat") -> list[dict]:
    """Full ranked board (not sliced to --top). Both lenses always computed;
    `basis` picks which is primary (sort order, value/vor), but
    availability_adjusted_rank, per_game_rank, and rank_delta are always present."""
    adj_board = compute_values(players, cfg, basis="availability_adjusted", replacement_mode=replacement_mode)
    pg_board = compute_values(players, cfg, basis="per_game", replacement_mode=replacement_mode)
    adj_by_id = {r["nba_id"]: r for r in adj_board}
    pg_by_id = {r["nba_id"]: r for r in pg_board}
    primary = pg_board if basis == "per_game" else adj_board

    rows = []
    for r in primary:
        nba_id = r["nba_id"]
        adj_rank = adj_by_id[nba_id]["rank"]
        pg_rank = pg_by_id[nba_id]["rank"]
        rows.append({
            "rank": r["rank"],
            "name": r["name"],
            "nba_id": nba_id,
            "position": "/".join(r.get("position") or ()) or "?",
            "value": r["value"],
            "vor": r["vor"],
            "availability_adjusted_rank": adj_rank,
            "per_game_rank": pg_rank,
            "rank_delta": adj_rank - pg_rank,
        })
    return rows


_CSV_FIELDS = ("rank", "name", "nba_id", "position", "value", "vor",
               "availability_adjusted_rank", "per_game_rank", "rank_delta")


def _print_table(rows: list[dict], league_name: str, description: str, basis: str, top: int,
                 replacement_mode: str = "flat") -> None:
    print(f"\n{league_name}  ({description})  basis={basis}  replacement={replacement_mode}")
    print(f"{'#':>4} {'player':<26} {'pos':>5} {'value':>8} {'vor':>7} {'adj#':>6} {'pg#':>6} {'delta':>6}")
    print("-" * 74)
    for row in rows[:top]:
        print(f"{row['rank']:>4} {(row['name'] or '?'):<26} {row['position']:>5} {row['value']:>8} {row['vor']:>7} "
              f"{row['availability_adjusted_rank']:>6} {row['per_game_rank']:>6} {row['rank_delta']:>+6}")
    if len(rows) > top:
        print(f"... {len(rows) - top} more (use --csv to export the full board)")


def _write_csv(path: str, rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def cmd_explain(args: argparse.Namespace, players: dict, cfg: LeagueConfig) -> int:
    explain(players, cfg, args.explain, basis=args.basis, replacement_mode=args.replacement)
    return 0


def cmd_compare(args: argparse.Namespace, players: dict) -> int:
    path_a, path_b = args.compare
    cfg_a = LeagueConfig.load(path_a)
    cfg_b = LeagueConfig.load(path_b)
    board_a = compute_values(players, cfg_a, basis=args.basis, replacement_mode=args.replacement)
    board_b = compute_values(players, cfg_b, basis=args.basis, replacement_mode=args.replacement)
    movers = _rank_movers(board_a, board_b)

    print(f"\n{cfg_a.name} -> {cfg_b.name}  (basis={args.basis})")
    print(f"\nBiggest risers under {cfg_b.name} (rank change):")
    for delta, name, was, now in sorted(movers, reverse=True)[:8]:
        print(f"   {(name or '?'):<26} {was:>3} -> {now:<3}  (+{delta})")
    print(f"\nBiggest fallers under {cfg_b.name} (rank change):")
    for delta, name, was, now in sorted(movers)[:8]:
        print(f"   {(name or '?'):<26} {was:>3} -> {now:<3}  ({delta})")
    return 0


def main(argv: list[str] | None = None) -> int:
    from util.console import configure_stdout_utf8

    configure_stdout_utf8()
    args = build_parser().parse_args(argv)
    args.basis = _normalize_basis(args.basis)

    if args.explain and args.compare:
        print("Error: --explain and --compare are mutually exclusive", file=sys.stderr)
        return 1
    if not args.league and not args.compare:
        print("Error: --league is required unless --compare is given", file=sys.stderr)
        return 1

    try:
        players, description = _load_players(args)

        if args.compare:
            return cmd_compare(args, players)

        cfg = LeagueConfig.load(args.league)

        if args.explain:
            return cmd_explain(args, players, cfg)

        rows = _board_rows(players, cfg, args.basis, args.replacement)
        _print_table(rows, cfg.name, description, args.basis, args.top, args.replacement)
        if args.csv:
            _write_csv(args.csv, rows)
            print(f"\nWrote {len(rows)} rows to {args.csv}")
        return 0
    except (FileNotFoundError, ValueError, pydantic.ValidationError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
