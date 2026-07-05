"""
engine/value.py -- M3, the value engine (core IP).

Turns player box-score lines (keyed by nba_id) plus a LeagueConfig into a ranked
draft board, parameterized entirely by the config. See VALUE_ENGINE.md for the math.

Category leagues: per-category z-scores over the draftable pool, ratio cats
volume-weighted, each capped at +/- Z_CAP so no single category can dominate,
summed over active (non-punt) cats, then value-over-replacement.
Points leagues: weighted sum of stats, then VOR.

basis:
  "total"    (default) -- per-game stats * games played, i.e. season totals, so
             availability counts. This is standard for a draft / total-value board.
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

Z_CAP = 3.0
# Availability softening: in total mode, stats scale by games**AVAIL_ALPHA.
#   1.0 -> pure season totals (availability counts fully)
#   0.5 -> sqrt softening (a star who misses time isn't buried) -- our default
#   0.0 -> per-game (availability ignored)
AVAIL_ALPHA = 0.5
# Per-game components scaled by the availability factor when basis="total".
SCALE_FIELDS = ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fga", "fta")


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
    """For basis='total', scale counting stats and attempts by games**avail_alpha."""
    if basis == "per_game":
        return players
    if basis != "total":
        raise ValueError(f"basis must be 'total' or 'per_game', got {basis!r}")
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


def compute_values(players, config, min_gp=20, pool_size=None, basis="total",
                   z_cap=Z_CAP, avail_alpha=AVAIL_ALPHA):
    """Ranked board (highest value first): {rank, nba_id, name, value, vor}."""
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

    ranked = sorted(value, key=value.get, reverse=True)
    replacement = value[ranked[min(pool_size, len(ranked)) - 1]]
    return [{"rank": n + 1, "nba_id": i, "name": eligible[i].get("name"),
             "value": round(value[i], 2), "vor": round(value[i] - replacement, 2)}
            for n, i in enumerate(ranked)]


def _print_board(title, board, n=30):
    print(f"\n{title}")
    print(f"{'#':>3}  {'player':<26} {'value':>7} {'vor':>7}")
    print("-" * 47)
    for row in board[:n]:
        print(f"{row['rank']:>3}  {(row['name'] or '?'):<26} {row['value']:>7} {row['vor']:>7}")


def _show_movers(base, other, label, n=8):
    base_rank = {r["nba_id"]: r["rank"] for r in base}
    print(f"\nBiggest risers under {label} (rank change):")
    movers = [(base_rank[r["nba_id"]] - r["rank"], r["name"], base_rank[r["nba_id"]], r["rank"])
              for r in other if r["nba_id"] in base_rank]
    for delta, name, was, now in sorted(movers, reverse=True)[:n]:
        print(f"   {(name or '?'):<26} {was:>3} -> {now:<3}  (+{delta})")


def _demo():
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.league_config import standard_9cat, punt_ft_9cat

    season = recent_completed_seasons(1)[0]
    players, src = get_season_boxscores(season)
    print(f"Building board on {season} ({len(players)} players, from {src}); basis=total")
    std = compute_values(players, standard_9cat())
    _print_board(f"Standard 9-Cat -- {season} top 30", std)
    punt = compute_values(players, punt_ft_9cat())
    _print_board(f"Punt FT% -- {season} top 30", punt)
    _show_movers(std, punt, "Punt FT%")


def _selftest():
    from engine.league_config import standard_9cat, punt_ft_9cat, points_league
    P = {
        1: dict(name="giannis_like", gp=51, pts=28, reb=11, ast=6, stl=0.9, blk=0.9,
                tov=3.2, fg3m=0.3, fg_pct=0.61, fga=18, ft_pct=0.60, fta=10),
        2: dict(name="sharp", gp=78, pts=22, reb=4, ast=6, stl=1.4, blk=0.3,
                tov=2.0, fg3m=3.6, fg_pct=0.47, fga=16, ft_pct=0.90, fta=5),
        3: dict(name="allround", gp=76, pts=20, reb=7, ast=7, stl=1.2, blk=0.6,
                tov=2.5, fg3m=2.0, fg_pct=0.50, fga=15, ft_pct=0.82, fta=6),
        4: dict(name="wing", gp=70, pts=14, reb=5, ast=2, stl=1.0, blk=0.5,
                tov=1.2, fg3m=1.8, fg_pct=0.46, fga=10, ft_pct=0.80, fta=3),
        5: dict(name="fillA", gp=72, pts=10, reb=4, ast=3, stl=0.7, blk=0.3,
                tov=1.5, fg3m=1.0, fg_pct=0.45, fga=9, ft_pct=0.78, fta=2),
        6: dict(name="fillB", gp=66, pts=9, reb=7, ast=1, stl=0.5, blk=1.2,
                tov=1.0, fg3m=0.2, fg_pct=0.56, fga=7, ft_pct=0.62, fta=4),
    }
    rank = lambda b, nm: next(r["rank"] for r in b if r["name"] == nm)

    capped = compute_values(P, standard_9cat(), min_gp=1, pool_size=6, z_cap=3)
    loose = compute_values(P, standard_9cat(), min_gp=1, pool_size=6, z_cap=100)
    assert rank(capped, "giannis_like") <= rank(loose, "giannis_like")

    two = {10: dict(name="ironman", gp=80, pts=18, reb=6, ast=4, stl=1, blk=0.5, tov=2,
                    fg3m=2, fg_pct=0.48, fga=13, ft_pct=0.8, fta=4),
           11: dict(name="fragile", gp=40, pts=18, reb=6, ast=4, stl=1, blk=0.5, tov=2,
                    fg3m=2, fg_pct=0.48, fga=13, ft_pct=0.8, fta=4)}
    tot = compute_values(two, standard_9cat(), min_gp=1, pool_size=2, basis="total")
    pg = compute_values(two, standard_9cat(), min_gp=1, pool_size=2, basis="per_game")
    assert rank(tot, "ironman") < rank(tot, "fragile")
    assert pg[0]["value"] == pg[1]["value"]

    pl = points_league()
    ptot = compute_values(two, pl, min_gp=1, pool_size=2, basis="total")
    assert rank(ptot, "ironman") < rank(ptot, "fragile")

    std = compute_values(P, standard_9cat(), min_gp=1, pool_size=6, basis="per_game")
    punt = compute_values(P, punt_ft_9cat(), min_gp=1, pool_size=6, basis="per_game")
    assert rank(punt, "giannis_like") < rank(std, "giannis_like")

    print("value selftest ok: cap bounds outliers, total rewards availability, points+punt work")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
