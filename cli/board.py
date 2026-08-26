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
from engine.divergence import CAVEAT, DEFAULT_THRESHOLD, projection_divergence
from engine.league_config import LeagueConfig, standard_9cat
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
    p.add_argument("--role-audit", action="store_true",
                   help="report players who changed teams for the upcoming season, ranked by projected "
                        "value -- the candidate list for data/role_overrides.json (--league optional)")
    p.add_argument("--divergence", action="store_true",
                   help="report players whose season-to-date production diverges materially from their "
                        "projected line, with a per-category why (--league optional). NOT a hot/cold "
                        "signal -- see the output header. Always per-game basis.")
    p.add_argument("--divergence-threshold", type=float, default=DEFAULT_THRESHOLD, metavar="X",
                   help=f"min |reliability-weighted divergence| to flag (default {DEFAULT_THRESHOLD}); "
                        "--divergence only")
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

    from engine.projection import (
        _next_season_label, load_role_overrides, load_rookies, project_players,
    )

    seasons = _projection_window(args.season)
    lines = [get_season_boxscores(s)[0] for s in seasons]
    # project_players only reads position/team off the newest season a player
    # appears in (history[0], always seasons[0] for players it projects) --
    # attaching to the rest would just be repeat work on data never read.
    lines[0] = attach_positions(lines[0], rosters=rosters)
    # C3: hand-entered 2026 draft class. load_rookies() skips (and logs) any
    # rookie whose stat line isn't filled in yet, so the shipped null-stat file
    # simply contributes nobody until it's populated.
    # C4: hand role overrides replace the minutes-trajectory multiplier for
    # players whose offseason team change the trajectory can't see. --explain
    # surfaces any that applied.
    projected, _aged = project_players(
        lines, rookies=load_rookies(), role_overrides=load_role_overrides())
    upcoming = _next_season_label(seasons[0])
    return projected, f"projected {upcoming} from {', '.join(seasons)}"


def _load_divergence_players(args: argparse.Namespace) -> tuple[dict, dict, str]:
    """(projected, actual, description) for --divergence: the projected line for a
    target season vs that season's real box scores. Target defaults to the latest
    completed season; the projection uses the three seasons before it, the same
    window backtest/validate.py uses. No rookies=/role_overrides= merge -- the
    target season here is historical (that's all the cache holds), and a future
    draft class / offseason override says nothing about a past season."""
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from ingest.nba_rosters import attach_positions, load_rosters_or_warn
    from engine.projection import project_players

    target = args.season or recent_completed_seasons(1)[0]
    y = int(target.split("-")[0])
    priors = [f"{yr}-{str(yr + 1)[2:]}" for yr in range(y - 1, y - 4, -1)]

    rosters = load_rosters_or_warn()
    actual = attach_positions(get_season_boxscores(target)[0], rosters=rosters)
    prior_lines = [get_season_boxscores(s)[0] for s in priors]
    prior_lines[0] = attach_positions(prior_lines[0], rosters=rosters)
    projected, _aged = project_players(prior_lines)
    return projected, actual, f"projected {target} from {', '.join(priors)}, vs {target} actuals"


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
            "is_rookie": bool(r.get("is_rookie")),
            "value": r["value"],
            "vor": r["vor"],
            "availability_adjusted_rank": adj_rank,
            "per_game_rank": pg_rank,
            "rank_delta": adj_rank - pg_rank,
        })
    return rows


_CSV_FIELDS = ("rank", "name", "nba_id", "position", "is_rookie", "value", "vor",
               "availability_adjusted_rank", "per_game_rank", "rank_delta")


def _print_table(rows: list[dict], league_name: str, description: str, basis: str, top: int,
                 replacement_mode: str = "flat") -> None:
    print(f"\n{league_name}  ({description})  basis={basis}  replacement={replacement_mode}")
    print(f"{'#':>4} {'player':<26} {'pos':>5} {'value':>8} {'vor':>7} {'adj#':>6} {'pg#':>6} {'delta':>6}")
    print("-" * 74)
    for row in rows[:top]:
        disp = (row['name'] or '?') + (' (R)' if row.get('is_rookie') else '')
        print(f"{row['rank']:>4} {disp:<26} {row['position']:>5} {row['value']:>8} {row['vor']:>7} "
              f"{row['availability_adjusted_rank']:>6} {row['per_game_rank']:>6} {row['rank_delta']:>+6}")
    if len(rows) > top:
        print(f"... {len(rows) - top} more (use --csv to export the full board)")
    if any(row.get('is_rookie') for row in rows[:top]):
        print("  (R) = 2026 draft class, hand-entered line from data/rookies_2026.json")


def _write_csv(path: str, rows: list[dict], fields: tuple | list = _CSV_FIELDS) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields))
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


def cmd_role_audit(args: argparse.Namespace, players: dict) -> int:
    """C4 diagnostic: who changed teams for the upcoming season, ranked by
    projected value -- the shortlist for a hand-written role override. Reports
    only; it never writes data/role_overrides.json."""
    from engine.projection import load_role_overrides
    from ingest.nba_rosters import team_changes

    cfg = LeagueConfig.load(args.league) if args.league else standard_9cat()
    changes = team_changes(offline=True)
    overrides = load_role_overrides()
    board = compute_values(players, cfg, basis=args.basis, replacement_mode=args.replacement)
    by_id = {r["nba_id"]: r for r in board}

    rows = []
    for pid, (old, new) in changes.items():
        r = by_id.get(pid)
        if not r:  # changed teams but not in the projected/eligible board (hurt, retired, <min GP)
            continue
        role_mult = (players.get(pid) or {}).get("role_mult")
        rows.append((r["value"], r["rank"], r["name"], role_mult, old, new, pid in overrides))
    rows.sort(key=lambda t: t[0], reverse=True)

    print(f"\n{cfg.name}: players who changed teams for the upcoming season, ranked by projected value")
    print(f"basis={args.basis}  |  role_mult = model's minutes-trajectory multiplier (pre-override)  |  "
          f"* = already in data/role_overrides.json")
    print(f"{'#':>4} {'player':<26} {'value':>8} {'role_mult':>10}  {'move':<12} ovr")
    print("-" * 74)
    for value, rank, name, role_mult, old, new, has_ovr in rows:
        rm = f"{role_mult:.3f}" if role_mult is not None else "  -  "
        print(f"{rank:>4} {(name or '?'):<26} {value:>8} {rm:>10}  {old + ' -> ' + new:<12} {'*' if has_ovr else ''}")
    if not rows:
        print("  (no team-changers intersect the projected board)")
    else:
        print(f"\n{len(rows)} team-changers on the board. "
              f"Low role_mult + bigger new role = prime override candidate.")
    return 0


def _divergence_csv(rows: list[dict], cat_keys: list[str]) -> tuple[list[dict], list[str]]:
    """Flatten projection_divergence() rows for CSV: cat_deltas -> dz_<stat>
    columns, reasons -> a single ' | '-joined string."""
    flat_fields = ["direction", "name", "nba_id", "position", "projected_rank",
                   "actual_rank", "rank_delta", "gp", "value_delta", "weighted_delta",
                   "reliability", "reasons"]
    fields = flat_fields + [f"dz_{k}" for k in cat_keys]
    out = []
    for r in rows:
        flat = {k: r[k] for k in flat_fields if k != "reasons"}
        flat["reasons"] = " | ".join(r["reasons"])
        for k in cat_keys:
            flat[f"dz_{k}"] = r["cat_deltas"].get(k, "")
        out.append(flat)
    return out, fields


def cmd_divergence(args: argparse.Namespace, projected: dict, actual: dict, description: str) -> int:
    cfg = LeagueConfig.load(args.league) if args.league else standard_9cat()
    rows = projection_divergence(projected, actual, cfg, threshold=args.divergence_threshold)
    over = [r for r in rows if r["direction"] == "over"]
    under = [r for r in rows if r["direction"] == "under"]

    pool = cfg.num_teams * sum(s.count for s in cfg.roster)
    print(f"\n{cfg.name} -- projection-divergence report  ({description})")
    print(f"basis=per_game  threshold={args.divergence_threshold}  draft pool={pool}")
    print(f"\n!! {CAVEAT}\n")

    def _section(title: str, rs: list[dict]) -> None:
        print(f"{title}  ({len(rs)})")
        if not rs:
            print("  (none)\n")
            return
        print(f"{'player':<26} {'pos':>4} {'proj#':>6} {'prod#':>6} {'d':>5} {'gp':>3}  why")
        print("-" * 100)
        for r in rs:
            print(f"{(r['name'] or '?'):<26} {r['position']:>4} {r['projected_rank']:>6} "
                  f"{r['actual_rank']:>6} {r['rank_delta']:>+5} {r['gp']:>3}  "
                  f"{' | '.join(r['reasons'])}")
        print()

    _section("PRODUCING ABOVE PROJECTION  (sell-high candidates IF this is a streak, not a miss)", over)
    _section("PRODUCING BELOW PROJECTION  (buy-low candidates IF this is a slump, not a miss)", under)

    if args.csv:
        cat_keys = [c.value for c in cfg.active_categories]
        flat, fields = _divergence_csv(rows, cat_keys)
        _write_csv(args.csv, flat, fields)
        print(f"Wrote {len(flat)} rows to {args.csv}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from util.console import configure_stdout_utf8

    configure_stdout_utf8()
    args = build_parser().parse_args(argv)
    args.basis = _normalize_basis(args.basis)

    if sum(bool(x) for x in (args.explain, args.compare, args.role_audit, args.divergence)) > 1:
        print("Error: --explain, --compare, --role-audit and --divergence are mutually exclusive",
              file=sys.stderr)
        return 1
    if not args.league and not args.compare and not args.role_audit and not args.divergence:
        print("Error: --league is required unless --compare, --role-audit or --divergence is given",
              file=sys.stderr)
        return 1

    try:
        if args.divergence:
            projected, actual, description = _load_divergence_players(args)
            return cmd_divergence(args, projected, actual, description)

        players, description = _load_players(args)

        if args.compare:
            return cmd_compare(args, players)
        if args.role_audit:
            return cmd_role_audit(args, players)

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
