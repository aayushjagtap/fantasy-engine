"""
backtest/validate.py -- validation harness.

Measures how well the projection predicts what ACTUALLY happened. It backtests:
project a season using only the seasons before it, then compare the projected
rankings to that season's real fantasy value (computed by the same value engine
on the actual box scores). Reality is the benchmark -- not expert opinion.

Metric: Spearman rank correlation between projected value and actual value.
Baseline: use last season's actual value as the prediction ("naive persistence").
Beating the baseline is the signal that the aging + multi-season projection is
adding something beyond "assume everyone repeats last year."

Run from the repo root:
    python backtest/validate.py             # backtests the latest complete season
    python backtest/validate.py --selftest  # checks the stats functions, no data needed
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# --------------------------------------------------------------------------- #
# Statistics (pure Python -- no scipy/numpy dependency)
# --------------------------------------------------------------------------- #

def _rankdata(values: list[float]) -> list[float]:
    """1-based ranks, ties share the average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x) ** 0.5
    vy = sum((b - my) ** 2 for b in y) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def spearman(a: list[float], b: list[float]) -> float:
    """Spearman rank correlation = Pearson on the ranks."""
    return _pearson(_rankdata(a), _rankdata(b))


def prior_seasons(target: str, n: int = 3) -> list[str]:
    """The n seasons immediately before `target`, newest first."""
    end = int(target.split("-")[0]) + 1
    return [f"{y - 1}-{str(y)[2:]}" for y in range(end - 1, end - 1 - n, -1)]


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #

def _values_by_id(board: list[dict]) -> dict:
    return {r["nba_id"]: r["value"] for r in board}


def _score_predictors(actual: dict, baseline: dict, ours_off: dict, ours_on: dict):
    """Score three predictors against `actual` on ONE common population.

    Previously each predictor's Spearman correlation was computed against its
    own intersection with `actual` (see the old per-predictor corr() closure),
    so baseline/off/on were each measured on a DIFFERENT population before
    being diffed against each other -- an apples-to-oranges bug. This computes
    the intersection across all four inputs up front and scores every
    predictor on exactly that population, returning (base_c, off_c, on_c, n).
    """
    common = sorted(set(actual) & set(baseline) & set(ours_off) & set(ours_on))
    act = [actual[i] for i in common]
    base_c = spearman([baseline[i] for i in common], act)
    off_c = spearman([ours_off[i] for i in common], act)
    on_c = spearman([ours_on[i] for i in common], act)
    return base_c, off_c, on_c, len(common)


def run_backtest(target_season: str | None = None, min_gp: int = 20) -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.projection import project_players, ROLE_DAMP
    from engine.value import compute_values
    from engine.league_config import standard_9cat

    if target_season is None:
        target_season = recent_completed_seasons(1)[0]
    priors = prior_seasons(target_season, 3)
    cfg = standard_9cat()

    print(f"Backtest target season: {target_season}")
    print(f"  our projection uses: {', '.join(priors)}")
    print(f"  naive baseline uses: {priors[0]} actuals\n")

    # Truth: actual fantasy value in the target season.
    actual_board = compute_values(get_season_boxscores(target_season)[0], cfg, min_gp=min_gp)
    actual = _values_by_id(actual_board)

    prior_lines = [get_season_boxscores(s)[0] for s in priors]
    # Ablation: our model with the role-trend ON vs OFF, to isolate its effect.
    proj_on = compute_values(project_players(prior_lines, role_damp=ROLE_DAMP)[0], cfg, min_gp=min_gp)
    proj_off = compute_values(project_players(prior_lines, role_damp=0.0)[0], cfg, min_gp=min_gp)
    ours_on = _values_by_id(proj_on)
    ours_off = _values_by_id(proj_off)

    # Baseline: last season's actual value, used as-is to predict this season.
    baseline = _values_by_id(compute_values(get_season_boxscores(priors[0])[0], cfg, min_gp=min_gp))

    base_c, off_c, on_c, n = _score_predictors(actual, baseline, ours_off, ours_on)

    actual_top30 = {r["nba_id"] for r in actual_board[:30]}
    hits = sum(1 for r in proj_on[:30] if r["nba_id"] in actual_top30)

    print(f"Players compared: {n}   (Spearman: 1.0 = perfect, higher = better)")
    print(f"  naive baseline (repeat last season):   {base_c:.3f}")
    print(f"  ours, role-trend OFF:                  {off_c:.3f}")
    print(f"  ours, role-trend ON:                   {on_c:.3f}")
    print(f"  -> role-trend adds:  {on_c - off_c:+.3f}   |   vs baseline:  {on_c - base_c:+.3f}")
    print(f"  Top-30 hit rate: {hits}/30 of our projected top 30 finished top 30\n")

    # Notable misses, ranked within the common set (insight, not just a number).
    ours = ours_on
    common = [i for i in ours if i in actual]
    pr = {i: r for r, i in enumerate(sorted(common, key=lambda i: ours[i], reverse=True), 1)}
    ar = {i: r for r, i in enumerate(sorted(common, key=lambda i: actual[i], reverse=True), 1)}
    name = {r["nba_id"]: r["name"] for r in proj_on}

    print("Biggest misses -- we ranked HIGH, they finished LOW (busts):")
    for i in sorted(common, key=lambda i: ar[i] - pr[i], reverse=True)[:5]:
        print(f"   {(name.get(i) or '?'):<26} projected #{pr[i]:<3} -> actual #{ar[i]}")
    print("\nBiggest misses -- we ranked LOW, they finished HIGH (breakouts we missed):")
    for i in sorted(common, key=lambda i: ar[i] - pr[i])[:5]:
        print(f"   {(name.get(i) or '?'):<26} projected #{pr[i]:<3} -> actual #{ar[i]}")


def _selftest() -> None:
    """Thin wrapper: runs tests/test_validate.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_validate.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("validate selftest ok: see tests/test_validate.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        from util.console import configure_stdout_utf8
        configure_stdout_utf8()
        seasons = [a for a in sys.argv[1:] if not a.startswith("-")]
        run_backtest(seasons[0] if seasons else None)
