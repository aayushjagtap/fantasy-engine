"""
engine/value.py -- M3, the value engine (core IP).

Turns player box-score lines (keyed by nba_id) plus a LeagueConfig into a ranked
draft board, parameterized entirely by the config. See VALUE_ENGINE.md for the math.

Category leagues: per-category z-scores over the draftable pool, ratio cats
volume-weighted, each capped at +/- Z_CAP so no single category can dominate,
summed over active (non-punt) cats, then value-over-replacement.
Points leagues: weighted sum of stats, then VOR.

basis:
  "availability_adjusted" (default; alias: "total") -- per-game stats scaled by
             games played ** AVAIL_ALPHA, so availability counts. Standard for a
             draft / total-value board. ("total" is accepted as an alias for
             backwards compatibility; despite the name this is NOT raw season
             totals -- see AVAIL_ALPHA below.)
  "per_game" -- rate stats only; ignores availability (useful for weekly streaming).

Run from the repo root:
    python engine/value.py            # current-season board (std 9-cat, then punt FT%)
    python engine/value.py --selftest # offline checks: cap, availability, points, punt
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.league_config import CATEGORY_META, ScoringType  # noqa: E402

# Roster slot name -> coarse position-eligibility group, or None for a
# position-blind slot (UTIL/BENCH, and any custom slot name we don't
# recognize -- unknown slots fall back to the flat pool by design). Positions
# ingested from CommonTeamRoster (C1) only distinguish G/F/C, not PG/SG/SF/PF,
# so PG and SG share the "G" group, SF and PF share "F".
SLOT_GROUP: dict[str, str | None] = {
    "PG": "G", "SG": "G", "G": "G",
    "SF": "F", "PF": "F", "F": "F",
    "C": "C",
    "UTIL": None, "BENCH": None,
}
REPLACEMENT_MODES = ("flat", "strict", "hybrid")

Z_CAP = 3.0
# Availability softening: in availability-adjusted mode, stats scale by games**AVAIL_ALPHA.
#   1.0 -> pure season totals (availability counts fully)
#   0.5 -> sqrt softening (a star who misses time isn't buried) -- our default
#   0.0 -> per-game (availability ignored)
AVAIL_ALPHA = 0.5
# Per-game components scaled by the availability factor when basis="availability_adjusted".
SCALE_FIELDS = ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fga", "fta", "fgm", "ftm", "min")


def _mean_std(xs):
    if not xs:
        return 0.0, 0.0
    mu = sum(xs) / len(xs)
    if len(xs) < 2:
        return mu, 0.0
    return mu, (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5


def _pool_ratio_pct(pool, pct_key, vol_key):
    tot = sum((p.get(vol_key) or 0) for p in pool.values())
    if tot == 0:
        return 0.0
    return sum((p.get(pct_key) or 0) * (p.get(vol_key) or 0) for p in pool.values()) / tot


def _cat_raw(players, pool, cat):
    meta = CATEGORY_META[cat]
    if meta.is_ratio:
        pool_pct = _pool_ratio_pct(pool, cat.value, meta.volume_stat)
        return {pid: (p.get(meta.volume_stat) or 0) * ((p.get(cat.value) or 0) - pool_pct)
                for pid, p in players.items()}
    return {pid: (p.get(cat.value) or 0) for pid, p in players.items()}


def _apply_basis(players, basis, avail_alpha=AVAIL_ALPHA):
    """For basis='availability_adjusted' (alias: 'total'), scale counting stats
    and attempts by games**avail_alpha."""
    if basis == "per_game":
        return players
    if basis not in ("availability_adjusted", "total"):
        raise ValueError(
            f"basis must be 'availability_adjusted' (alias: 'total') or "
            f"'per_game', got {basis!r}"
        )
    out = {}
    for i, p in players.items():
        factor = (p.get("gp") or 0) ** avail_alpha
        q = dict(p)
        for f in SCALE_FIELDS:
            if q.get(f) is not None:
                q[f] = q[f] * factor
        out[i] = q
    return out


def _category_totals(players, config, pool_ids, z_cap=Z_CAP):
    """Sum of weighted, capped per-category z-scores, baselined on `pool_ids`."""
    pool = {i: players[i] for i in pool_ids}
    totals = {pid: 0.0 for pid in players}
    for cat in config.active_categories:
        meta = CATEGORY_META[cat]
        raw = _cat_raw(players, pool, cat)
        mu, sigma = _mean_std([raw[i] for i in pool_ids])
        if sigma == 0:
            continue
        weight = config.categories[cat].weight
        sign = 1.0 if meta.higher_is_better else -1.0
        for pid in players:
            d = (raw[pid] - mu) / sigma
            d = max(-z_cap, min(z_cap, d))     # no single category can dominate
            totals[pid] += weight * sign * d
    return totals


def _draft_pool_ids(scaled, config, pool_size, z_cap=Z_CAP):
    pass1 = _category_totals(scaled, config, list(scaled), z_cap)
    return sorted(pass1, key=pass1.get, reverse=True)[:pool_size]


def _position_demand(config, mode):
    """(demand_by_group, flex_demand) -- league-wide player counts by position
    group ('G'/'F'/'C'), per `mode`. Both 'strict' and 'hybrid' count dedicated
    slots (PG/SG/G, SF/PF/F, C) toward their group's demand. They differ on
    UTIL/BENCH (and any unrecognized custom slot name): 'hybrid' leaves that
    demand on the flat/global pool -- UTIL/BENCH are position-blind by
    construction, so folding their count into G/F/C would inflate every
    position's scarcity based on bench-slot count, a roster-construction
    artifact, not a scarcity signal. 'strict' instead distributes it across
    G/F/C proportionally to each group's existing share of dedicated slots,
    so every roster spot is attributed to a position and there's no flat
    tier at all."""
    demand = {"G": 0, "F": 0, "C": 0}
    flex = 0
    for slot in config.roster:
        group = SLOT_GROUP.get(slot.position)
        n = slot.count * config.num_teams
        if group:
            demand[group] += n
        else:
            flex += n
    if mode == "strict" and flex:
        dedicated_total = sum(demand.values())
        if dedicated_total:
            for group in demand:
                demand[group] += round(flex * demand[group] / dedicated_total)
        flex = 0
    return demand, flex


def _group_replacement_levels(value, positions, ranked_all, demand):
    """{'G'/'F'/'C': value} -- the value of the Nth-best position-eligible
    player for each group with demand > 0, ranked by the same global `value`
    used everywhere else (the z-score baseline pool stays flat regardless of
    replacement_mode -- only the replacement reference point is positional).
    A group with demand but zero position-tagged candidates (no position data
    ingested yet) is omitted; callers fall back to the flat replacement."""
    levels = {}
    for group, n in demand.items():
        if n <= 0:
            continue
        candidates = [pid for pid in ranked_all if group in (positions.get(pid) or ())]
        if not candidates:
            continue
        idx = min(n, len(candidates)) - 1
        levels[group] = value[candidates[idx]]
    return levels


def compute_values(players, config, min_gp=20, pool_size=None, basis="availability_adjusted",
                   z_cap=Z_CAP, avail_alpha=AVAIL_ALPHA, replacement_mode="flat"):
    """Ranked board (highest VOR first): {rank, nba_id, name, position, value, vor}.

    replacement_mode ('flat' default, or 'strict'/'hybrid'; see _position_demand)
    controls the replacement-level REFERENCE POINT subtracted from `value` to
    get `vor` -- it does not change `value` itself (the category z-scores are
    always baselined on the flat draftable pool). Under 'flat' this is a single
    scalar for every player, so ranking by value or by vor is identical (that's
    why the pre-C2 code could rank by value alone). Under 'strict'/'hybrid' the
    replacement point varies by position, so value-order and vor-order diverge
    -- the board is ranked by vor so positional scarcity actually moves players,
    not just the displayed number.

    Points leagues have no positional-scarcity concept baked into the value
    calc (no category z-scoring, so no position-eligible sub-pools to rank
    within) and always use the flat replacement regardless of replacement_mode.
    """
    if replacement_mode not in REPLACEMENT_MODES:
        raise ValueError(f"replacement_mode must be one of {REPLACEMENT_MODES}, got {replacement_mode!r}")
    eligible = {i: p for i, p in players.items() if (p.get("gp") or 0) >= min_gp}
    if not eligible:
        return []
    if pool_size is None:
        total_roster = sum(getattr(s, "count", 0) for s in config.roster) or 13
        pool_size = config.num_teams * total_roster
    pool_size = max(1, min(pool_size, len(eligible)))
    scaled = _apply_basis(eligible, basis, avail_alpha)

    if config.scoring_type is ScoringType.POINTS:
        value = {i: sum(coef * (p.get(stat) or 0) for stat, coef in config.point_values.items())
                 for i, p in scaled.items()}
    else:
        pool_ids = _draft_pool_ids(scaled, config, pool_size, z_cap)
        value = _category_totals(scaled, config, pool_ids, z_cap)

    by_value = sorted(value, key=value.get, reverse=True)
    flat_replacement = value[by_value[min(pool_size, len(by_value)) - 1]]

    if replacement_mode == "flat" or config.scoring_type is ScoringType.POINTS:
        def replacement_for(pid):
            return flat_replacement
    else:
        positions = {i: eligible[i].get("position") for i in eligible}
        demand, _flex = _position_demand(config, replacement_mode)
        group_levels = _group_replacement_levels(value, positions, by_value, demand)

        def replacement_for(pid):
            applicable = [group_levels[g] for g in (positions.get(pid) or ()) if g in group_levels]
            return min(applicable) if applicable else flat_replacement

    vor = {i: value[i] - replacement_for(i) for i in value}
    ranked = sorted(vor, key=vor.get, reverse=True)
    return [{"rank": n + 1, "nba_id": i, "name": eligible[i].get("name"),
             "position": eligible[i].get("position") or (),
             "value": round(value[i], 2), "vor": round(vor[i], 2)}
            for n, i in enumerate(ranked)]


def _print_board(title, board, n=30):
    print(f"\n{title}")
    print(f"{'#':>3}  {'player':<26} {'value':>7} {'vor':>7}")
    print("-" * 47)
    for row in board[:n]:
        print(f"{row['rank']:>3}  {(row['name'] or '?'):<26} {row['value']:>7} {row['vor']:>7}")


def _rank_movers(base, other):
    """(delta, name, was_rank, now_rank) for players in both boards.

    delta = base_rank - other_rank: positive means the player moved UP
    (better/lower rank number) under `other` relative to `base`.
    """
    base_rank = {r["nba_id"]: r["rank"] for r in base}
    return [(base_rank[r["nba_id"]] - r["rank"], r["name"], base_rank[r["nba_id"]], r["rank"])
            for r in other if r["nba_id"] in base_rank]


def _show_movers(base, other, label, n=8):
    print(f"\nBiggest risers under {label} (rank change):")
    movers = _rank_movers(base, other)
    for delta, name, was, now in sorted(movers, reverse=True)[:n]:
        print(f"   {(name or '?'):<26} {was:>3} -> {now:<3}  (+{delta})")


def _demo():
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.league_config import standard_9cat, punt_ft_9cat
    from util.console import configure_stdout_utf8

    configure_stdout_utf8()
    season = recent_completed_seasons(1)[0]
    players, src = get_season_boxscores(season)
    print(f"Building board on {season} ({len(players)} players, from {src}); basis=availability_adjusted")
    std = compute_values(players, standard_9cat())
    _print_board(f"Standard 9-Cat -- {season} top 30", std)
    punt = compute_values(players, punt_ft_9cat())
    _print_board(f"Punt FT% -- {season} top 30", punt)
    _show_movers(std, punt, "Punt FT%")


def _selftest():
    """Thin wrapper: runs tests/test_value.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_value.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("value selftest ok: see tests/test_value.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
