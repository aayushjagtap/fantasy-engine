"""
engine/value.py -- M3, the value engine (core IP).

Turns a dict of player box-score lines (from ingest, keyed by nba_id) plus a
LeagueConfig into a ranked draft board, parameterized entirely by the config.
Swap the config -> the board re-sorts. See VALUE_ENGINE.md for the math.

Category leagues: per-category z-scores over the draftable pool, ratio cats
(FG%, FT%) volume-weighted, summed over active (non-punt) categories, then
value-over-replacement. Points leagues: weighted sum of stats, then VOR.

Run from the repo root:
    python engine/value.py            # builds the current-season board (std 9-cat, then punt FT%)
    python engine/value.py --selftest # offline check of the math on synthetic players
"""

from __future__ import annotations

import os
import sys

# Make the repo root importable so this runs as `python engine/value.py` from root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.league_config import CATEGORY_META, ScoringType  # noqa: E402


# --------------------------------------------------------------------------- #
# Math helpers
# --------------------------------------------------------------------------- #

def _mean_std(xs: list[float]) -> tuple[float, float]:
    """Population mean and std. Std is 0 for degenerate inputs (caller skips those)."""
    if not xs:
        return 0.0, 0.0
    mu = sum(xs) / len(xs)
    if len(xs) < 2:
        return mu, 0.0
    var = sum((x - mu) ** 2 for x in xs) / len(xs)
    return mu, var ** 0.5


def _pool_ratio_pct(pool: dict, pct_key: str, vol_key: str) -> float:
    """Volume-weighted pool average for a ratio category, e.g. pool FG% = sum(FGM)/sum(FGA)."""
    total_att = sum((p.get(vol_key) or 0) for p in pool.values())
    if total_att == 0:
        return 0.0
    total_makes = sum((p.get(pct_key) or 0) * (p.get(vol_key) or 0) for p in pool.values())
    return total_makes / total_att


def _cat_raw(players: dict, pool: dict, cat) -> dict:
    """
    Per-player scalar for one category:
      - counting cat -> the stat itself
      - ratio cat    -> volume-weighted impact = attempts * (pct - pool_pct)
    Ratio impact uses the pool's volume-weighted pct so it measures how much a
    player moves the aggregate, not just their raw percentage.
    """
    meta = CATEGORY_META[cat]
    if meta.is_ratio:
        pool_pct = _pool_ratio_pct(pool, cat.value, meta.volume_stat)
        out = {}
        for pid, p in players.items():
            att = p.get(meta.volume_stat) or 0
            pct = p.get(cat.value) or 0
            out[pid] = att * (pct - pool_pct)
        return out
    return {pid: (p.get(cat.value) or 0) for pid, p in players.items()}


def _category_totals(players: dict, config, pool_ids: list) -> dict:
    """Sum of weighted per-category z-scores for every player, baselined on `pool_ids`."""
    pool = {i: players[i] for i in pool_ids}
    totals = {pid: 0.0 for pid in players}
    for cat in config.active_categories:
        meta = CATEGORY_META[cat]
        raw = _cat_raw(players, pool, cat)
        mu, sigma = _mean_std([raw[i] for i in pool_ids])
        if sigma == 0:
            continue
        weight = config.categories[cat].weight
        sign = 1.0 if meta.higher_is_better else -1.0  # TOV: fewer is better
        for pid in players:
            totals[pid] += weight * sign * (raw[pid] - mu) / sigma
    return totals


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def compute_values(players: dict, config, min_gp: int = 20, pool_size: int | None = None) -> list[dict]:
    """
    Ranked board (highest value first). Each entry:
      {rank, nba_id, name, value, vor}
    value  = summed weighted z (category) or weighted stat total (points)
    vor    = value above the last draftable player (replacement level)
    """
    eligible = {i: p for i, p in players.items() if (p.get("gp") or 0) >= min_gp}
    if not eligible:
        return []

    if pool_size is None:
        total_roster = sum(getattr(s, "count", 0) for s in config.roster) or 13
        pool_size = config.num_teams * total_roster
    pool_size = max(1, min(pool_size, len(eligible)))

    if config.scoring_type is ScoringType.POINTS:
        value = {
            i: sum(coef * (p.get(stat) or 0) for stat, coef in config.point_values.items())
            for i, p in eligible.items()
        }
    else:
        # Pass 1: baseline on all eligible players. Pass 2: re-baseline on the
        # top `pool_size` from pass 1 (the actually-draftable players).
        pass1 = _category_totals(eligible, config, list(eligible))
        pool_ids = sorted(pass1, key=pass1.get, reverse=True)[:pool_size]
        value = _category_totals(eligible, config, pool_ids)

    ranked = sorted(value, key=value.get, reverse=True)
    replacement = value[ranked[min(pool_size, len(ranked)) - 1]]
    return [
        {
            "rank": n + 1,
            "nba_id": i,
            "name": eligible[i].get("name"),
            "value": round(value[i], 2),
            "vor": round(value[i] - replacement, 2),
        }
        for n, i in enumerate(ranked)
    ]


# --------------------------------------------------------------------------- #
# Demo + self-test
# --------------------------------------------------------------------------- #

def _print_board(title: str, board: list[dict], n: int = 30) -> None:
    print(f"\n{title}")
    print(f"{'#':>3}  {'player':<26} {'value':>7} {'vor':>7}")
    print("-" * 47)
    for row in board[:n]:
        print(f"{row['rank']:>3}  {(row['name'] or '?'):<26} {row['value']:>7} {row['vor']:>7}")


def _show_movers(base: list[dict], other: list[dict], label: str, n: int = 8) -> None:
    base_rank = {r["nba_id"]: r["rank"] for r in base}
    print(f"\nBiggest risers under {label} (rank change):")
    movers = [
        (base_rank[r["nba_id"]] - r["rank"], r["name"], base_rank[r["nba_id"]], r["rank"])
        for r in other if r["nba_id"] in base_rank
    ]
    for delta, name, was, now in sorted(movers, reverse=True)[:n]:
        print(f"   {(name or '?'):<26} {was:>3} -> {now:<3}  (+{delta})")


def _demo() -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.league_config import standard_9cat, punt_ft_9cat

    season = recent_completed_seasons(1)[0]  # always the current/latest season
    players, src = get_season_boxscores(season)
    print(f"Building board on {season} ({len(players)} players, from {src})")

    std = compute_values(players, standard_9cat())
    _print_board(f"Standard 9-Cat -- {season} top 30", std)

    punt = compute_values(players, punt_ft_9cat())
    _print_board(f"Punt FT% -- {season} top 30", punt)

    _show_movers(std, punt, "Punt FT%")
    print("\n(Value uses last season's actuals as a naive projection -- the smarter")
    print(" multi-year projection baseline is the next milestone.)")


def _selftest() -> None:
    from engine.league_config import standard_9cat, punt_ft_9cat

    # Synthetic players. "brick_big": elite blk/reb/pts, awful FT% on high volume.
    # Under standard 9-cat the bad FT% drags him down; punting FT% should lift him.
    players = {
        1: dict(name="brick_big", gp=70, pts=24, reb=13, ast=2, stl=0.8, blk=2.8,
                tov=3.0, fg3m=0.1, fg_pct=0.62, fga=14, ft_pct=0.48, fta=8),
        2: dict(name="sharp_guard", gp=70, pts=22, reb=4, ast=6, stl=1.4, blk=0.3,
                tov=2.2, fg3m=3.8, fg_pct=0.47, fga=16, ft_pct=0.90, fta=5),
        3: dict(name="allrounder", gp=70, pts=20, reb=7, ast=7, stl=1.2, blk=0.6,
                tov=2.5, fg3m=2.0, fg_pct=0.50, fga=15, ft_pct=0.82, fta=6),
        4: dict(name="role_wing", gp=70, pts=12, reb=5, ast=2, stl=1.0, blk=0.5,
                tov=1.2, fg3m=1.8, fg_pct=0.46, fga=9, ft_pct=0.78, fta=2),
        5: dict(name="filler_a", gp=70, pts=9, reb=4, ast=3, stl=0.6, blk=0.2,
                tov=1.5, fg3m=1.0, fg_pct=0.44, fga=8, ft_pct=0.75, fta=2),
        6: dict(name="filler_b", gp=70, pts=8, reb=6, ast=1, stl=0.5, blk=1.0,
                tov=1.0, fg3m=0.2, fg_pct=0.55, fga=6, ft_pct=0.60, fta=3),
    }

    std = compute_values(players, standard_9cat(), min_gp=1, pool_size=6)
    punt = compute_values(players, punt_ft_9cat(), min_gp=1, pool_size=6)
    rank_std = {r["name"]: r["rank"] for r in std}
    rank_punt = {r["name"]: r["rank"] for r in punt}

    # Core assertion: punting FT% must improve the brick big's rank.
    assert rank_punt["brick_big"] < rank_std["brick_big"], (rank_std, rank_punt)
    # The elite-FT sharpshooter should not gain from punting FT% (it removes his edge).
    assert rank_punt["sharp_guard"] >= rank_std["sharp_guard"]
    # Board is fully ranked and VOR is monotonic non-increasing with rank.
    vors = [r["vor"] for r in std]
    assert vors == sorted(vors, reverse=True)
    # Replacement-level player sits at ~0 VOR.
    assert abs(std[5]["vor"]) < 1e-9

    print("selftest ok: punt lifts the low-FT big, sharpshooter unaffected, VOR monotonic")
    print(f"  standard: {[r['name'] for r in std]}")
    print(f"  punt FT%: {[r['name'] for r in punt]}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
