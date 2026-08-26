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
from crosswalk.names import normalize_name


def category_contributions(players, config, target_id, *, min_gp=20, pool_size=None,
                           basis="availability_adjusted", z_cap=Z_CAP,
                           avail_alpha=AVAIL_ALPHA, pool_ids=None):
    """Per-category value breakdown for one player, as structured data.

    Returns {StatCategory: {
        "z":           weighted, signed, capped z contribution -- these sum to
                       the player's category-league board value,
        "capped":      bool, the raw z hit +/- z_cap,
        "player":      the player's per-game stat (a percentage for ratio cats),
        "pool_avg":    draft-pool average of that same per-game stat
                       (volume-weighted pool pct for ratio cats),
        "is_ratio":    bool,
        "volume_stat": attempts stat backing a ratio cat, else None,
    }} keyed by StatCategory -- or None if target_id isn't eligible, or the
    league is POINTS (no per-category z-scoring exists there).

    This is the machinery behind explain(), pulled out so other modules
    (engine/divergence.py) can diff a player's actual vs projected per-category
    profile without re-deriving the z math or scraping explain()'s stdout.

    pool_ids overrides the draftable baseline pool (default: recomputed via
    _draft_pool_ids). engine/divergence.py passes ONE fixed pool for both the
    projected and the actual line, so the resulting z-delta reflects only the
    player's movement, not a season-to-season shift in the pool itself.
    """
    if config.scoring_type is ScoringType.POINTS:
        return None
    eligible = {i: p for i, p in players.items() if (p.get("gp") or 0) >= min_gp}
    if target_id not in eligible:
        return None
    if pool_size is None:
        total_roster = sum(getattr(s, "count", 0) for s in config.roster) or 13
        pool_size = config.num_teams * total_roster
    pool_size = max(1, min(pool_size, len(eligible)))

    scaled = _apply_basis(eligible, basis, avail_alpha)
    if pool_ids is None:
        pool_ids = _draft_pool_ids(scaled, config, pool_size, z_cap)
    else:
        pool_ids = [i for i in pool_ids if i in scaled]
    pool_s = {i: scaled[i] for i in pool_ids}
    pool_pg = {i: eligible[i] for i in pool_ids}

    p = eligible[target_id]
    out = {}
    for cat in config.active_categories:
        meta = CATEGORY_META[cat]
        raw_s = _cat_raw(scaled, pool_s, cat)
        mu_s, sig_s = _mean_std([raw_s[i] for i in pool_ids])
        w = config.categories[cat].weight
        sign = 1.0 if meta.higher_is_better else -1.0
        raw_d = (raw_s[target_id] - mu_s) / sig_s if sig_s else 0.0
        d = max(-z_cap, min(z_cap, raw_d))
        if meta.is_ratio:
            player_disp = p.get(cat.value) or 0.0
            pool_disp = _pool_ratio_pct(pool_pg, cat.value, meta.volume_stat)
        else:
            raw_pg = _cat_raw(eligible, pool_pg, cat)
            mu_pg, _ = _mean_std([raw_pg[i] for i in pool_ids])
            player_disp = p.get(cat.value) or 0.0
            pool_disp = mu_pg
        out[cat] = {
            "z": w * sign * d,
            "capped": bool(sig_s) and abs(raw_d) > z_cap,
            "player": player_disp,
            "pool_avg": pool_disp,
            "is_ratio": meta.is_ratio,
            "volume_stat": meta.volume_stat,
        }
    return out


def explain(players, config, target_name, min_gp=20, pool_size=None, basis="availability_adjusted",
            z_cap=Z_CAP, avail_alpha=AVAIL_ALPHA, replacement_mode="flat"):
    eligible = {i: p for i, p in players.items() if (p.get("gp") or 0) >= min_gp}
    if not eligible:
        print("no eligible players")
        return None, None
    if pool_size is None:
        total_roster = sum(getattr(s, "count", 0) for s in config.roster) or 13
        pool_size = config.num_teams * total_roster
    pool_size = max(1, min(pool_size, len(eligible)))

    # Exact match is accent/case-insensitive via normalize_name, so "Luka Doncic",
    # "luka doncic", and "Luka Dončić" all resolve to the same player.
    target_key = normalize_name(target_name)
    tid = next((i for i, p in eligible.items()
                if normalize_name(p.get("name") or "") == target_key), None)
    if tid is None:
        # "did you mean" suggestions: full normalized query as a substring first
        # (precise, still accent-insensitive), falling back to just the last
        # token (surname) so old partial-query behavior keeps working.
        last_key = normalize_name(target_name.split()[-1]) if target_name.split() else ""
        near = [p.get("name") for p in eligible.values()
                if (target_key and target_key in normalize_name(p.get("name") or ""))
                or (last_key and last_key in normalize_name(p.get("name") or ""))]
        print(f"'{target_name}' not found among {len(eligible)} eligible (>= {min_gp} GP).")
        if near:
            print("  did you mean:", ", ".join(near[:6]))
        return None, None

    board = compute_values(players, config, min_gp=min_gp, pool_size=pool_size,
                           basis=basis, z_cap=z_cap, avail_alpha=avail_alpha,
                           replacement_mode=replacement_mode)
    rank = next((r["rank"] for r in board if r["nba_id"] == tid), None)
    p = eligible[tid]
    position = "/".join(p.get("position") or ()) or "?"
    print(f"\n{p.get('name')}  (proj age {p.get('age')}, {round(p.get('gp') or 0)} GP, pos {position})"
          f"  ->  rank #{rank}  (replacement_mode={replacement_mode})")
    if p.get("role_mult") is not None:
        eff = p["role_mult"]
        trend = "expanding role" if eff > 1 else "shrinking role" if eff < 1 else "flat role"
        if p.get("role_override") is not None:
            pre = p.get("role_trend_mult")
            pre_s = f"{pre:.3f}" if pre is not None else "n/a"
            print(f"  role trend multiplier: {eff:.3f}  ({trend}; MANUAL OVERRIDE from "
                  f"data/role_overrides.json, model computed {pre_s} from minutes trajectory)")
        else:
            print(f"  role trend multiplier: {eff:.3f}  ({trend}, from minutes trajectory)")
    if p.get("is_rookie"):
        print("  rookie: 2026 draft class -- hand-entered line from data/rookies_2026.json, not a model projection")

    if config.scoring_type is ScoringType.POINTS:
        print(f"{'stat':<8} {'per-game':>9} {'points':>9}")
        print("-" * 30)
        for stat, coef in config.point_values.items():
            print(f"{stat:<8} {(p.get(stat) or 0):>9.2f} {coef * (p.get(stat) or 0):>9.2f}")
        return rank, None

    contribs = category_contributions(players, config, tid, min_gp=min_gp, pool_size=pool_size,
                                      basis=basis, z_cap=z_cap, avail_alpha=avail_alpha)

    print(f"basis: {basis}  |  z capped at +/-{z_cap}   (stats shown per-game for readability)")
    print(f"{'category':<8} {'player':>8} {'pool avg':>9} {'z (weighted)':>13}")
    print("-" * 42)
    total, ordered = 0.0, []
    for cat in config.active_categories:
        c = contribs[cat]
        total += c["z"]
        mark = "*" if c["capped"] else " "
        if c["is_ratio"]:
            player_disp = f"{c['player']:.3f}"
            pool_disp = f"{c['pool_avg']:.3f}"
        else:
            player_disp = f"{c['player']:.2f}"
            pool_disp = f"{c['pool_avg']:.2f}"
        ordered.append((c["z"], cat.value))
        print(f"{cat.value:<8} {player_disp:>8} {pool_disp:>9} {c['z']:>12.2f}{mark}")
    print("-" * 42)
    print(f"{'TOTAL':<8} {'':>8} {'':>9} {total:>12.2f}")
    ordered.sort(reverse=True)
    carried = [f"{n} (+{z:.2f})" for z, n in ordered if z > 0][:3]
    dragged = [f"{n} ({z:.2f})" for z, n in ordered if z < 0][-3:]
    if carried:
        print("  carried by:", ", ".join(carried))
    if dragged:
        print("  dragged by:", ", ".join(dragged))
    print("  (* = category hit the cap; z reflects availability-adjusted value)")
    return rank, total


def _selftest():
    """Thin wrapper: runs tests/test_diagnose.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_diagnose.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("diagnose selftest ok: see tests/test_diagnose.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        # demo path uses the real ingest/projection when run in the repo
        from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
        from engine.projection import load_role_overrides, load_rookies, project_players
        from engine.league_config import standard_9cat
        from util.console import configure_stdout_utf8
        configure_stdout_utf8()
        name = " ".join(a for a in sys.argv[1:] if not a.startswith("-")).strip() or "Jamal Murray"
        seasons = recent_completed_seasons(3)
        lines = [get_season_boxscores(s)[0] for s in seasons]
        projected, _ = project_players(lines, rookies=load_rookies(),
                                      role_overrides=load_role_overrides())
        explain(projected, standard_9cat(), name)
