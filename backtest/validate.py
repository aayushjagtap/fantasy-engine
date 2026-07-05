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


def run_backtest(target_season: str | None = None, min_gp: int = 20) -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from engine.projection import project_players
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

    # Our model: aging + multi-season projection built from prior seasons only.
    prior_lines = [get_season_boxscores(s)[0] for s in priors]
    proj_board = compute_values(project_players(prior_lines)[0], cfg, min_gp=min_gp)
    ours = _values_by_id(proj_board)

    # Baseline: last season's actual value, used as-is to predict this season.
    baseline = _values_by_id(compute_values(get_season_boxscores(priors[0])[0], cfg, min_gp=min_gp))

    def corr(pred: dict):
        common = [i for i in pred if i in actual]
        return spearman([pred[i] for i in common], [actual[i] for i in common]), len(common)

    our_c, n_our = corr(ours)
    base_c, n_base = corr(baseline)

    actual_top30 = {r["nba_id"] for r in actual_board[:30]}
    hits = sum(1 for r in proj_board[:30] if r["nba_id"] in actual_top30)

    print(f"Players compared: {n_our}")
    print(f"  Spearman  ours vs actual:      {our_c:.3f}")
    print(f"  Spearman  baseline vs actual:  {base_c:.3f}   (naive: repeat last season)")
    delta = our_c - base_c
    verdict = "beats baseline" if delta > 0.005 else ("ties baseline" if abs(delta) <= 0.005 else "TRAILS baseline")
    print(f"  -> our projection {verdict} by {delta:+.3f}")
    print(f"  Top-30 hit rate: {hits}/30 of our projected top 30 finished top 30\n")

    # Notable misses, ranked within the common set (insight, not just a number).
    common = [i for i in ours if i in actual]
    pr = {i: r for r, i in enumerate(sorted(common, key=lambda i: ours[i], reverse=True), 1)}
    ar = {i: r for r, i in enumerate(sorted(common, key=lambda i: actual[i], reverse=True), 1)}
    name = {r["nba_id"]: r["name"] for r in proj_board}
    moves = sorted(common, key=lambda i: ar[i] - pr[i])  # neg = we underrated (breakout)

    print("Biggest misses -- we ranked HIGH, they finished LOW (busts):")
    for i in sorted(common, key=lambda i: ar[i] - pr[i], reverse=True)[:5]:
        print(f"   {(name.get(i) or '?'):<26} projected #{pr[i]:<3} -> actual #{ar[i]}")
    print("\nBiggest misses -- we ranked LOW, they finished HIGH (breakouts we missed):")
    for i in moves[:5]:
        print(f"   {(name.get(i) or '?'):<26} projected #{pr[i]:<3} -> actual #{ar[i]}")


def _selftest() -> None:
    assert abs(spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) - 1.0) < 1e-9   # perfect
    assert abs(spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) + 1.0) < 1e-9   # inverse
    assert _rankdata([10, 10, 20]) == [1.5, 1.5, 3.0]                     # ties average
    assert prior_seasons("2025-26", 3) == ["2024-25", "2023-24", "2022-23"]
    s = spearman([1, 2, 3, 4, 5], [1, 2, 3, 5, 4])
    assert 0.0 < s < 1.0, s
    print("validate selftest ok: spearman (perfect/inverse/partial), tie handling, season math")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        run_backtest()
