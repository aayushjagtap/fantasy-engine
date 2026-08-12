"""tests/test_validate.py -- moved from backtest/validate.py's --selftest,
plus a new offline regression test for the common-population bug (A4).
"""
from __future__ import annotations

from backtest.validate import _rankdata, _score_predictors, prior_seasons, spearman


def test_spearman_perfect_and_inverse():
    assert abs(spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) - 1.0) < 1e-9   # perfect
    assert abs(spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) + 1.0) < 1e-9   # inverse


def test_spearman_partial():
    s = spearman([1, 2, 3, 4, 5], [1, 2, 3, 5, 4])
    assert 0.0 < s < 1.0, s


def test_rankdata_ties_average():
    assert _rankdata([10, 10, 20]) == [1.5, 1.5, 3.0]


def test_prior_seasons():
    assert prior_seasons("2025-26", 3) == ["2024-25", "2023-24", "2022-23"]


# --- A4: corr() used to compute `common` separately per predictor, so
# base/off/on were each scored on a DIFFERENT population and then diffed.
# _score_predictors must compute ONE intersection across all four inputs
# up front and score every predictor on exactly that population. ---

def test_score_predictors_uses_one_common_population():
    # actual has 1..6; baseline is missing 6; ours_off is missing 5; ours_on is missing 1.
    # True intersection across all four is {2, 3, 4}.
    actual   = {1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0, 6: 0.0}
    baseline = {1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0}
    ours_off = {1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 6: 0.0}
    ours_on  = {2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0, 6: 0.0}

    base_c, off_c, on_c, n = _score_predictors(actual, baseline, ours_off, ours_on)

    assert n == 3
    # On the common population {2, 3, 4}, every predictor equals `actual` exactly,
    # so every correlation should be a perfect 1.0 -- if the old per-predictor
    # intersection bug were still present, predictors would be scored on
    # different, larger populations and this identity would break.
    assert abs(base_c - 1.0) < 1e-9
    assert abs(off_c - 1.0) < 1e-9
    assert abs(on_c - 1.0) < 1e-9


def test_score_predictors_empty_intersection():
    actual = {1: 1.0}
    baseline = {2: 1.0}
    ours_off = {3: 1.0}
    ours_on = {4: 1.0}
    base_c, off_c, on_c, n = _score_predictors(actual, baseline, ours_off, ours_on)
    assert n == 0
    assert (base_c, off_c, on_c) == (0.0, 0.0, 0.0)
