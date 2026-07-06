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

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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
                    role_damp: float = ROLE_DAMP) -> tuple[dict, int]:
    """
    seasons_newest_first: list of {nba_id: line}, newest season first.
    Returns ({nba_id: projected_line}, n_players_with_age) for the upcoming season.

    Only players who appeared in the MOST RECENT season are projected -- otherwise
    the union of several seasons pulls in players now out of the league, filling the
    deep end with erratic small-sample lines. We project who's actually around.
    """
    if not seasons_newest_first:
        return {}, 0
    current_ids = set(seasons_newest_first[0])
    projected: dict[int, dict] = {}
    aged = 0

    for pid in current_ids:
        history = [s[pid] for s in seasons_newest_first if pid in s][: len(RECENCY_WEIGHTS)]
        if not history:
            continue
        base = _weighted_line(history)
        latest = history[0]

        latest_age = latest.get("age")
        proj_age = (latest_age + target_age_offset) if latest_age is not None else None
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
        role_mult = _role_trend_mult([h.get("min") for h in history], damp=role_damp)
        for comp in ROLE_COMPONENTS:
            line[comp] *= role_mult
        line["role_mult"] = round(role_mult, 3)

        # Injury-history-aware games projection. Blend the recency-weighted games
        # with the player's BEST recent season: a one-off injury is rescued by
        # healthy years (the max term lifts it), while a chronically injured player
        # has no healthy year to rescue them, so they stay taxed. Then apply the
        # durability aging curve and cap at a full season.
        best_recent_gp = max((h.get("gp") or 0) for h in history)
        expected_gp = 0.6 * base["gp"] + 0.4 * best_recent_gp
        line["gp"] = min(82.0, expected_gp * age_multiplier(proj_age, "durability"))

        # Derive points and percentages from the aged components so they stay consistent.
        line["pts"] = 2 * line["fgm"] + line["fg3m"] + line["ftm"]
        line["fg_pct"] = (line["fgm"] / line["fga"]) if line["fga"] else 0.0
        line["ft_pct"] = (line["ftm"] / line["fta"]) if line["fta"] else 0.0
        line["name"] = latest.get("name")
        line["age"] = proj_age
        projected[pid] = line

    return projected, aged


def _next_season_label(latest: str) -> str:
    start = int(latest.split("-")[0]) + 1
    return f"{start}-{str(start + 1)[2:]}"


def _demo() -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.league_config import standard_9cat
    from engine.value import compute_values, _print_board

    seasons = recent_completed_seasons(3)  # newest first
    lines = [get_season_boxscores(s)[0] for s in seasons]
    projected, aged = project_players(lines)
    upcoming = _next_season_label(seasons[0])

    print(f"Projecting {upcoming} from {', '.join(seasons)}")
    print(f"  {len(projected)} players projected, {aged} with age data for aging curves\n")

    # Two lenses: availability-adjusted (expected value, the draft default) and
    # per-game (ceiling / talent, ignoring games). Good drafters hold both.
    proj_board = compute_values(projected, standard_9cat(), basis="total")
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
    # age_multiplier shape
    assert age_multiplier(22, "ast") > 1.0          # young -> growth
    assert age_multiplier(34, "blk") < 1.0          # old + athletic -> decline
    assert age_multiplier(29, "scoring") == 1.0     # plateau between peak and decline
    assert age_multiplier(None, "scoring") == 1.0   # no age -> no adjustment

    # Synthetic 3-season histories (newest first) with age fields.
    young = [
        dict(name="youngster", age=22, gp=70, min=32, fgm=8, fga=16, fg3m=2, ftm=4, fta=5,
             reb=6, ast=5, stl=1.4, blk=0.8, tov=2.0),
        dict(name="youngster", age=21, gp=68, min=30, fgm=7, fga=15, fg3m=1.6, ftm=3.5, fta=4.5,
             reb=5.5, ast=4.2, stl=1.2, blk=0.7, tov=1.9),
        dict(name="youngster", age=20, gp=60, min=26, fgm=6, fga=13, fg3m=1.2, ftm=3, fta=4,
             reb=5, ast=3.5, stl=1.0, blk=0.6, tov=1.8),
    ]
    vet = [
        dict(name="vet", age=34, gp=65, min=30, fgm=7, fga=15, fg3m=1.5, ftm=3, fta=3.5,
             reb=8, ast=4, stl=1.0, blk=1.6, tov=2.0),
        dict(name="vet", age=33, gp=70, min=32, fgm=7.5, fga=15.5, fg3m=1.6, ftm=3.2, fta=3.7,
             reb=8.5, ast=4.2, stl=1.1, blk=1.8, tov=2.1),
        dict(name="vet", age=32, gp=72, min=33, fgm=8, fga=16, fg3m=1.7, ftm=3.4, fta=3.9,
             reb=9, ast=4.5, stl=1.2, blk=2.0, tov=2.2),
    ]
    seasons = [{1: young[0], 2: vet[0]}, {1: young[1], 2: vet[1]}, {1: young[2], 2: vet[2]}]
    projected, aged = project_players(seasons)
    assert aged == 2

    # Role trend: youngster's minutes rise (32<-30<-26) -> mult>1; vet falls -> mult<1.
    assert projected[1]["role_mult"] > 1.0, projected[1]["role_mult"]
    assert projected[2]["role_mult"] < 1.0, projected[2]["role_mult"]

    base_young = _weighted_line(young)
    base_vet = _weighted_line(vet)
    # Young player's assists projected ABOVE their weighted history (growth).
    assert projected[1]["ast"] > base_young["ast"]
    # Vet's blocks projected BELOW their weighted history (athletic decline).
    assert projected[2]["blk"] < base_vet["blk"]
    # Efficiency is preserved: projected FG% ~ the weighted-history FG%.
    hist_fg = base_young["fgm"] / base_young["fga"]
    assert abs(projected[1]["fg_pct"] - hist_fg) < 1e-9

    print("selftest ok: young player grows, vet's athletic stats fade, FG% preserved")
    print(f"  youngster proj age {projected[1]['age']}: ast {base_young['ast']:.2f} -> {projected[1]['ast']:.2f}")
    print(f"  vet proj age {projected[2]['age']}: blk {base_vet['blk']:.2f} -> {projected[2]['blk']:.2f}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
