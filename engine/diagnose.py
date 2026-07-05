"""engine/diagnose.py -- per-category value breakdown (matches the board's basis + cap)."""
from __future__ import annotations
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from engine.value import (
    _apply_basis, _cat_raw, _draft_pool_ids, _mean_std, _pool_ratio_pct, compute_values,
    Z_CAP, AVAIL_ALPHA,
)
from engine.league_config import CATEGORY_META, ScoringType


def explain(players, config, target_name, min_gp=20, pool_size=None, basis="total",
            z_cap=Z_CAP, avail_alpha=AVAIL_ALPHA):
    eligible = {i: p for i, p in players.items() if (p.get("gp") or 0) >= min_gp}
    if not eligible:
        print("no eligible players")
        return None, None
    if pool_size is None:
        total_roster = sum(getattr(s, "count", 0) for s in config.roster) or 13
        pool_size = config.num_teams * total_roster
    pool_size = max(1, min(pool_size, len(eligible)))

    tid = next((i for i, p in eligible.items()
                if (p.get("name") or "").lower() == target_name.lower()), None)
    if tid is None:
        last = target_name.split()[-1].lower()
        near = [p.get("name") for p in eligible.values() if last in (p.get("name") or "").lower()]
        print(f"'{target_name}' not found among {len(eligible)} eligible (>= {min_gp} GP).")
        if near:
            print("  did you mean:", ", ".join(near[:6]))
        return None, None

    board = compute_values(players, config, min_gp=min_gp, pool_size=pool_size,
                           basis=basis, z_cap=z_cap, avail_alpha=avail_alpha)
    rank = next((r["rank"] for r in board if r["nba_id"] == tid), None)
    p = eligible[tid]
    print(f"\n{p.get('name')}  (proj age {p.get('age')}, {round(p.get('gp') or 0)} GP)  ->  rank #{rank}")

    if config.scoring_type is ScoringType.POINTS:
        print(f"{'stat':<8} {'per-game':>9} {'points':>9}")
        print("-" * 30)
        for stat, coef in config.point_values.items():
            print(f"{stat:<8} {(p.get(stat) or 0):>9.2f} {coef * (p.get(stat) or 0):>9.2f}")
        return rank, None

    scaled = _apply_basis(eligible, basis, avail_alpha)
    pool_ids = _draft_pool_ids(scaled, config, pool_size, z_cap)
    pool_s = {i: scaled[i] for i in pool_ids}
    pool_pg = {i: eligible[i] for i in pool_ids}

    print(f"basis: {basis}  |  z capped at +/-{z_cap}   (stats shown per-game for readability)")
    print(f"{'category':<8} {'player':>8} {'pool avg':>9} {'z (weighted)':>13}")
    print("-" * 42)
    total, contribs = 0.0, []
    for cat in config.active_categories:
        meta = CATEGORY_META[cat]
        raw_s = _cat_raw(scaled, pool_s, cat)
        mu_s, sig_s = _mean_std([raw_s[i] for i in pool_ids])
        w = config.categories[cat].weight
        sign = 1.0 if meta.higher_is_better else -1.0
        d = max(-z_cap, min(z_cap, (raw_s[tid] - mu_s) / sig_s)) if sig_s else 0.0
        z = w * sign * d
        total += z
        capped = "*" if sig_s and abs((raw_s[tid] - mu_s) / sig_s) > z_cap else " "
        if meta.is_ratio:
            player_disp = f"{(p.get(cat.value) or 0):.3f}"
            pool_disp = f"{_pool_ratio_pct(pool_pg, cat.value, meta.volume_stat):.3f}"
        else:
            raw_pg = _cat_raw(eligible, pool_pg, cat)
            mu_pg, _ = _mean_std([raw_pg[i] for i in pool_ids])
            player_disp = f"{(p.get(cat.value) or 0):.2f}"
            pool_disp = f"{mu_pg:.2f}"
        contribs.append((z, cat.value))
        print(f"{cat.value:<8} {player_disp:>8} {pool_disp:>9} {z:>12.2f}{capped}")
    print("-" * 42)
    print(f"{'TOTAL':<8} {'':>8} {'':>9} {total:>12.2f}")
    contribs.sort(reverse=True)
    carried = [f"{n} (+{z:.2f})" for z, n in contribs if z > 0][:3]
    dragged = [f"{n} ({z:.2f})" for z, n in contribs if z < 0][-3:]
    if carried:
        print("  carried by:", ", ".join(carried))
    if dragged:
        print("  dragged by:", ", ".join(dragged))
    print("  (* = category hit the cap; z reflects season-total value, availability included)")
    return rank, total


def _selftest():
    from engine.league_config import standard_9cat
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
    rank, total = explain(P, standard_9cat(), "allround", min_gp=1, pool_size=6)
    board = compute_values(P, standard_9cat(), min_gp=1, pool_size=6)
    board_value = next(r["value"] for r in board if r["name"] == "allround")
    assert abs(total - board_value) < 0.02, (total, board_value)
    print(f"\ndiagnose selftest ok: contributions sum to board value ({total:.2f} == {board_value})")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        # demo path uses the real ingest/projection when run in the repo
        from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
        from engine.projection import project_players
        from engine.league_config import standard_9cat
        name = " ".join(a for a in sys.argv[1:] if not a.startswith("-")).strip() or "Jamal Murray"
        seasons = recent_completed_seasons(3)
        lines = [get_season_boxscores(s)[0] for s in seasons]
        projected, _ = project_players(lines)
        explain(projected, standard_9cat(), name)
