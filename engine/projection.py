"""
engine/projection.py -- the projection baseline.

Turns several past seasons of box-score lines into a projected line for the
UPCOMING season, which the value engine then ranks. Two pieces:

  1. Recency-weighted averaging -- recent seasons count more (default 0.5/0.3/0.2
     over the last three seasons a player appears in).
  2. Aging curves -- a per-stat-group multiplier by projected age.

Design choice worth understanding: aging is applied to OPPORTUNITY and volume
(minutes, shot volume, and the athletic counting stats -- steals, blocks, boards),
NOT to efficiency. Shooting percentages are derived from the aged makes/attempts,
so FG%/FT% stay roughly stable while volume and athletic production move with age.
That mirrors how players actually age: they lose minutes and explosiveness before
they lose their touch. Efficiency is sticky; opportunity is what erodes.

This is a transparent PRIOR, not a data-derived curve. Deriving the curves
empirically from the cached seasons is a clean later refinement (and a good one).

Run from the repo root:
    python engine/projection.py            # projects the upcoming season, ranks it, shows age-driven movers
    python engine/projection.py --selftest # offline check of weighting + aging, no network
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Hand-maintained data files merged into the projection (C3 rookies, C4 role
# overrides). Both are optional -- a missing file degrades to "no rookies" /
# "no overrides", never an error. See each file's own _comment for its contract.
ROOKIES_PATH = os.path.join(_ROOT, "data", "rookies_2026.json")
ROLE_OVERRIDES_PATH = os.path.join(_ROOT, "data", "role_overrides.json")

# Most-recent-first weights over the last N seasons a player appears in.
RECENCY_WEIGHTS = [0.5, 0.3, 0.2]

# Per aging group: (peak_age, decline_start_age, decline_per_year, growth_per_year).
# Athletic stats (stl, blk) peak early and fall fast; skill/volume stats hold longer.
AGING = {
    "scoring":    (27, 31, 0.020, 0.020),
    "reb":        (27, 31, 0.020, 0.012),
    "ast":        (28, 32, 0.015, 0.020),
    "stl":        (25, 29, 0.030, 0.020),
    "blk":        (26, 30, 0.030, 0.015),
    "minutes":    (28, 31, 0.030, 0.020),
    "durability": (28, 31, 0.030, 0.010),
}

# Which aging group each projected per-game component uses. fgm/fga/ftm/fta/fg3m
# share "scoring" so their ratios (FG%, FT%) survive the aging multiply unchanged.
COMPONENT_GROUP = {
    "min": "minutes", "gp": "durability",
    "fgm": "scoring", "fga": "scoring", "fg3m": "scoring", "ftm": "scoring", "fta": "scoring",
    "reb": "reb", "ast": "ast", "stl": "stl", "blk": "blk",
    # "tov" intentionally absent -> flat (turnovers aren't cleanly age-driven)
}

COMPONENTS = ["min", "gp", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]
# Opportunity components that scale with role (everything except games played).
ROLE_COMPONENTS = [c for c in COMPONENTS if c != "gp"]
# How much of a minutes trend to extrapolate forward (damped, not blind).
ROLE_DAMP = 0.4

# Modeling assumptions for players returning after missed seasons; these are
# conservative availability adjustments, not fitted injury probabilities.
MAX_RETURN_MISSING_SEASONS = 2
RETURN_GP_FACTOR_PER_MISSED_SEASON = 0.75


def _role_trend_mult(minutes_newest_first, damp=ROLE_DAMP, min_floor=8.0):
    """
    Opportunity multiplier from a player's minutes trajectory. Rising minutes give
    >1 (role expanding), falling give <1 (role shrinking). Damped so we nudge toward
    the trend rather than blindly extrapolating, and clamped to a sane band.
    Note: a breakout from near-zero minutes has no trend to read -- that case needs
    external opportunity data (a real minutes projection), not this signal.
    """
    mins = [m or 0 for m in minutes_newest_first]
    if len(mins) < 2:
        return 1.0
    recent, older = mins[0], mins[1:]
    prev = sum(older) / len(older)
    if recent < min_floor and prev < min_floor:
        return 1.0
    rel = (recent - prev) / max(prev, min_floor)
    return max(0.85, min(1.20, 1.0 + damp * rel))


def age_multiplier(age: float | None, group: str | None) -> float:
    """Aging multiplier for a stat group at a projected age. Clamped to [0.80, 1.12]."""
    if age is None or group not in AGING:
        return 1.0
    peak, decline_start, decline_rate, growth_rate = AGING[group]
    m = 1.0
    if age < peak:
        m += growth_rate * min(peak - age, 4)      # growth window capped at 4 yrs
    if age > decline_start:
        m -= decline_rate * min(age - decline_start, 8)  # decline window capped at 8 yrs
    return max(0.80, min(1.12, m))


def _weighted_line(history: list[dict]) -> dict:
    """Recency-weighted per-game components from a player's seasons (newest first)."""
    weights = RECENCY_WEIGHTS[: len(history)]
    wsum = sum(weights) or 1.0
    return {
        comp: sum(w * (h.get(comp) or 0) for w, h in zip(weights, history)) / wsum
        for comp in COMPONENTS
    }


def project_players(seasons_newest_first: list[dict], target_age_offset: int = 1,
                    role_damp: float = ROLE_DAMP, rookies: dict | None = None,
                    role_overrides: dict | None = None,
                    season_labels: list[str] | None = None,
                    target_season: str | None = None,
                    current_roster: dict[int, dict] | None = None) -> tuple[dict, int]:
    """
    seasons_newest_first: list of {nba_id: line}, newest season first.
    Returns ({nba_id: projected_line}, n_players_with_age) for the upcoming season.

    Without current_roster, only players in the newest statistical season are
    projected, preserving historical backtest behavior. With a current_roster
    and season labels, current-roster members with recent older NBA history can
    also be projected. The roster is an eligibility signal, not health evidence.

    rookies ({nba_id: projected_line}, e.g. from load_rookies()): season-scoped
    draft-class lines with no prior NBA history to project from. Merged after
    the projection loop via setdefault, so a real projection always wins an ID
    collision. Each carries is_rookie=True for board labeling.

    role_overrides ({nba_id: multiplier}, e.g. from load_role_overrides()):
    applied AFTER _role_trend_mult and REPLACING it -- the minutes-trajectory
    signal can't see an offseason team change. The trajectory result is still
    computed and kept as line["role_trend_mult"]; line["role_override"] records
    the override; line["role_mult"] is the effective (post-override) value.
    """
    if not seasons_newest_first:
        return {}, 0
    if season_labels is not None and len(season_labels) != len(seasons_newest_first):
        raise ValueError("season_labels must match seasons_newest_first")
    current_ids = set(seasons_newest_first[0])
    candidate_ids = set(current_ids)
    if current_roster is not None:
        if season_labels is None or target_season is None:
            raise ValueError("current_roster requires season_labels and target_season")
        candidate_ids.intersection_update(current_roster)
        target_start = int(target_season.split("-")[0])
        for pid in set(current_roster) - current_ids:
            appearances = [i for i, season in enumerate(seasons_newest_first) if pid in season]
            if not appearances:
                continue
            last_idx = appearances[0]
            last_start = int(season_labels[last_idx].split("-")[0])
            missing = target_start - last_start - 1
            if 0 < missing <= MAX_RETURN_MISSING_SEASONS:
                candidate_ids.add(pid)

    projected: dict[int, dict] = {}
    aged = 0

    for pid in candidate_ids:
        history_records = [(season_labels[i] if season_labels else None, season[pid])
                           for i, season in enumerate(seasons_newest_first) if pid in season]
        history_records = history_records[:len(RECENCY_WEIGHTS)]
        history = [row for _season, row in history_records]
        if not history:
            continue
        base = _weighted_line(history)
        latest = history[0]
        last_actual_season = history_records[0][0]
        if target_season is not None and last_actual_season is not None:
            elapsed_seasons = max(1, int(target_season.split("-")[0]) -
                                  int(last_actual_season.split("-")[0]))
        else:
            elapsed_seasons = target_age_offset
        missing_seasons = max(0, elapsed_seasons - 1)

        latest_age = latest.get("age")
        proj_age = (latest_age + elapsed_seasons) if latest_age is not None else None
        if proj_age is not None:
            aged += 1

        line = {}
        for comp in COMPONENTS:
            mult = age_multiplier(proj_age, COMPONENT_GROUP.get(comp))
            line[comp] = base[comp] * mult

        # Role/opportunity trend: nudge volume by the player's minutes trajectory,
        # so an expanding role projects up and a fading vet down. Targets the
        # veteran-collapse busts the backtest exposed. (Breakouts from near-zero
        # minutes still need external opportunity data -- that's the next step.)
        # C4: a hand override, if present for this player, REPLACES the trajectory
        # multiplier (the trajectory can't see an offseason team change). The
        # trajectory value is still recorded so --explain can show both.
        consecutive_history = (missing_seasons == 0 and (len(history_records) < 2 or
                               history_records[0][0] is None or history_records[1][0] is None or
                               int(history_records[0][0].split("-")[0]) -
                               int(history_records[1][0].split("-")[0]) == 1))
        trend_mult = (_role_trend_mult([h.get("min") for h in history], damp=role_damp)
                      if consecutive_history else 1.0)
        override = (role_overrides or {}).get(pid)
        role_mult = float(override) if override is not None else trend_mult
        for comp in ROLE_COMPONENTS:
            line[comp] *= role_mult
        line["role_mult"] = round(role_mult, 3)
        line["role_trend_mult"] = round(trend_mult, 3)
        if override is not None:
            line["role_override"] = float(override)

        # Injury-history-aware games projection. Blend the recency-weighted games
        # with the player's BEST recent season: a one-off injury is rescued by
        # healthy years (the max term lifts it), while a chronically injured player
        # has no healthy year to rescue them, so they stay taxed. Then apply the
        # durability aging curve and cap at a full season.
        best_recent_gp = max((h.get("gp") or 0) for h in history)
        expected_gp = 0.6 * base["gp"] + 0.4 * best_recent_gp
        return_gp_adjustment = (RETURN_GP_FACTOR_PER_MISSED_SEASON ** missing_seasons
                                if current_roster is not None and missing_seasons else 1.0)
        line["gp"] = min(82.0, expected_gp * age_multiplier(proj_age, "durability") *
                         return_gp_adjustment)

        # Derive points and percentages from the aged components so they stay consistent.
        line["pts"] = 2 * line["fgm"] + line["fg3m"] + line["ftm"]
        line["fg_pct"] = (line["fgm"] / line["fga"]) if line["fga"] else 0.0
        line["ft_pct"] = (line["ftm"] / line["fta"]) if line["fta"] else 0.0
        roster_player = current_roster.get(pid) if current_roster is not None else None
        line["name"] = ((roster_player.get("name") if roster_player else None)
                        or latest.get("name"))
        line["age"] = proj_age
        line["position"] = (roster_player.get("position", ()) if roster_player is not None
                            else latest.get("position", ()))
        line["team"] = (roster_player.get("team") if roster_player is not None
                        else latest.get("team"))
        line["last_actual_season"] = last_actual_season
        line["missing_seasons"] = missing_seasons
        line["used_returner_fallback"] = bool(current_roster is not None and pid not in current_ids)
        line["expected_games_adjustment"] = return_gp_adjustment
        line["roster_source"] = (f"CommonTeamRoster {target_season}"
                                 if current_roster is not None else None)
        projected[pid] = line

    # Merge season-scoped rookie estimates without replacing a real projection.
    for pid, rookie_line in (rookies or {}).items():
        if current_roster is not None and pid not in current_roster:
            continue
        if (target_season is not None and rookie_line.get("rookie_season") is not None
                and rookie_line["rookie_season"] != target_season):
            continue
        projected.setdefault(pid, rookie_line)

    return projected, aged


def project_current_roster(n_seasons: int = 3) -> tuple[dict, int, str]:
    """Build the upcoming board from cached stats and a complete target roster."""
    from ingest.nba_boxscores import (
        OfflineCacheMissError, get_season_boxscores, recent_completed_seasons,
    )
    from ingest.nba_rosters import get_season_rosters

    seasons = recent_completed_seasons(n_seasons)
    target = _next_season_label(seasons[0])
    try:
        current_roster, coverage = get_season_rosters(target, offline=True)
    except OfflineCacheMissError as exc:
        raise ValueError(f"complete {target} roster cache required for current projections; "
                         f"a team roster response is missing: {exc}. Refresh locally with "
                         "`python ingest/nba_rosters.py --force`.") from exc
    if coverage["teams_total"] == 0 or coverage["teams_with_data"] != coverage["teams_total"]:
        raise ValueError(f"complete {target} roster cache required for current projections; "
                         f"found {coverage['teams_with_data']}/{coverage['teams_total']} team rosters. "
                         "Refresh locally with `python ingest/nba_rosters.py --force`.")
    if coverage.get("duplicate_ids"):
        duplicates = ", ".join(str(row["nba_id"]) for row in coverage["duplicate_ids"])
        raise ValueError(f"duplicate NBA IDs in the {target} roster cache: {duplicates}")
    try:
        lines = [get_season_boxscores(season, offline=True)[0] for season in seasons]
    except OfflineCacheMissError as exc:
        raise ValueError(f"cached season statistics required for current projections: {exc}") from exc
    projected, aged = project_players(
        lines, rookies=load_rookies(target_season=target),
        role_overrides=load_role_overrides(target_season=target),
        season_labels=seasons, target_season=target, current_roster=current_roster)
    return projected, aged, target


def _next_season_label(latest: str) -> str:
    start = int(latest.split("-")[0]) + 1
    return f"{start}-{str(start + 1)[2:]}"


# Stat keys a rookie entry must ALL carry (non-null) to be projectable -- the
# per-game components the value engine reads. pts/fg_pct/ft_pct are derived.
_ROOKIE_REQUIRED_STATS = ("gp", "min", "fgm", "fga", "fg3m", "ftm", "fta",
                          "reb", "ast", "stl", "blk", "tov")


def _rookie_line(entry: dict, baseline: dict | None = None) -> dict | None:
    """One data/rookies_2026.json entry -> a projected line or incomplete skip."""
    if any(entry.get(k) is None for k in _ROOKIE_REQUIRED_STATS):
        from engine.rookie_baseline import make_rookie_line
        return make_rookie_line(entry, baseline)
    line = {k: float(entry[k]) for k in _ROOKIE_REQUIRED_STATS}
    line["pts"] = 2 * line["fgm"] + line["fg3m"] + line["ftm"]
    line["fg_pct"] = (line["fgm"] / line["fga"]) if line["fga"] else 0.0
    line["ft_pct"] = (line["ftm"] / line["fta"]) if line["fta"] else 0.0
    line["name"] = entry.get("name")
    line["age"] = entry.get("age")
    pos = entry.get("position") or ()
    line["position"] = tuple(pos) if isinstance(pos, (list, tuple)) else (pos,)
    line["team"] = entry.get("team")
    line["role_mult"] = 1.0  # no history -> no trajectory to trend
    line["is_rookie"] = True
    line["projection_source"] = entry.get("projection_source", "manual")
    line["rookie_season"] = "2026-27"
    line["projection_is_estimate"] = True
    line["draft_pick"] = entry.get("draft_pick")
    line["comparator_sample_size"] = entry.get("comparator_sample_size")
    line["comparator_position"] = entry.get("comparator_position")
    line["comparator_criteria"] = entry.get("comparator_criteria")
    line["manual_opportunity_multiplier"] = entry.get("manual_opportunity_multiplier")
    return line


def load_rookies(path: str = ROOKIES_PATH, verbose: bool = True,
                 target_season: str | None = None) -> dict[int, dict]:
    """{nba_id: projected_line} for the season-scoped first-round class.

    When target_season is supplied, the file's top-level season must match;
    this keeps current hand data out of historical projections.

    Incomplete entries are filled only from supported historical position
    baselines. Unsupported lines are skipped, never converted to zeroes. The
    skipped count is logged to stderr (verbose=True); cli.rookie_audit explains
    specific omissions. A missing file returns {}."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if target_season is not None and doc.get("season") != target_season:
        return {}
    out: dict[int, dict] = {}
    skipped = 0
    baselines = {}
    if any(any(entry.get(k) is None for k in _ROOKIE_REQUIRED_STATS)
           for entry in doc.get("rookies", [])):
        try:
            from engine.rookie_baseline import load_comparator_baselines
            baselines, _meta = load_comparator_baselines()
        except (OSError, ValueError, KeyError, IndexError):
            baselines = {}
    for entry in doc.get("rookies", []):
        line = _rookie_line(entry, baselines.get(entry.get("baseline_position")))
        if line is None:
            skipped += 1
            continue
        out[int(entry["nba_id"])] = line
    if verbose and skipped:
        print(f"load_rookies: {len(out)} rookie(s) projected, {skipped} "
              f"skipped (incomplete or unsupported baseline in {os.path.basename(path)})",
              file=sys.stderr)
    return out


def load_role_overrides(path: str = ROLE_OVERRIDES_PATH,
                        target_season: str | None = None) -> dict[int, float]:
    """{nba_id: multiplier} hand-edited role overrides. Keys starting with '_'
    (comment / notes blocks) are ignored. When target_season is supplied, the
    file's top-level _season must match. A missing file returns {}."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if target_season is not None and doc.get("_season") != target_season:
        return {}
    return {int(k): float(v) for k, v in doc.items() if not str(k).startswith("_")}


def _demo() -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.league_config import standard_9cat
    from engine.value import compute_values, _print_board
    from util.console import configure_stdout_utf8

    configure_stdout_utf8()
    seasons = recent_completed_seasons(3)  # newest first
    lines = [get_season_boxscores(s, offline=True)[0] for s in seasons]
    projected, aged, upcoming = project_current_roster()

    print(f"Projecting {upcoming} from {', '.join(seasons)}")
    print(f"  {len(projected)} players projected, {aged} with age data for aging curves\n")

    # Two lenses: availability-adjusted (expected value, the draft default) and
    # per-game (ceiling / talent, ignoring games). Good drafters hold both.
    proj_board = compute_values(projected, standard_9cat(), basis="availability_adjusted")
    _print_board(f"Projected {upcoming} -- availability-adjusted (expected value), top 30", proj_board)

    pg_board = compute_values(projected, standard_9cat(), basis="per_game")
    _print_board(f"Projected {upcoming} -- per-game (ceiling / talent), top 15", pg_board, n=15)

    # How the projection moves players vs simply using last season's raw board.
    # NOTE: these swings are driven mostly by role / minutes changes across seasons,
    # not by age -- the aging curve shows up mainly at the top of the board (e.g.
    # Wembanyama passing Jokic). Down-weighting small-sample seasons is the next
    # refinement that will tame the deep-pool volatility. We scope this readout to
    # the draftable range so it reflects real draft-relevant movement, not noise.
    raw_board = compute_values(lines[0], standard_9cat())
    raw_rank = {r["nba_id"]: r["rank"] for r in raw_board}
    proj_age = {pid: p.get("age") for pid, p in projected.items()}
    DRAFTABLE = 150

    moves = [
        (raw_rank[r["nba_id"]] - r["rank"], r["name"], proj_age.get(r["nba_id"]),
         raw_rank[r["nba_id"]], r["rank"])
        for r in proj_board if r["nba_id"] in raw_rank
    ]
    risers = sorted((m for m in moves if m[4] <= DRAFTABLE and m[0] > 0), reverse=True)[:8]
    fallers = sorted(m for m in moves if m[3] <= DRAFTABLE and m[0] < 0)[:8]

    print(f"\nBiggest risers into the top {DRAFTABLE} (projected {upcoming} vs raw {seasons[0]}):")
    for delta, name, age, was, now in risers:
        print(f"   {(name or '?'):<26} age {age}  {was:>3} -> {now:<3}  (+{delta})")
    print(f"\nBiggest fallers out of the top {DRAFTABLE}:")
    for delta, name, age, was, now in fallers:
        print(f"   {(name or '?'):<26} age {age}  {was:>3} -> {now:<3}  ({delta})")


def _selftest() -> None:
    """Thin wrapper: runs tests/test_projection.py under pytest.

    Pass -s through (python engine/projection.py --selftest -s) to see the
    projection-magnitude eyeball check printed by the moved test.
    """
    import sys

    import pytest

    extra = ["-s"] if "-s" in sys.argv else []
    rc = pytest.main(["-q", *extra, os.path.join(_ROOT, "tests", "test_projection.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("selftest ok: see tests/test_projection.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
