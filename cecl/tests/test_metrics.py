from __future__ import annotations

import math

import numpy as np
import pytest

from cecl import metrics


def test_log_losses() -> None:
    y = [0, 1, 2]
    p = np.array([[0.9, 0.05, 0.05], [0.2, 0.7, 0.1], [0.3, 0.1, 0.6]])
    expected = -(math.log(0.9) + math.log(0.7) + math.log(0.6)) / 3
    assert metrics.log_loss(y, p, [1, 1, 1]) == pytest.approx(expected)
    assert metrics.log_loss(y, p, [2, 1, 1]) == pytest.approx(
        -(2 * math.log(0.9) + math.log(0.7) + math.log(0.6)) / 4
    )
    binary = -(math.log(0.95) + math.log(0.7) + math.log(0.9)) / 3
    assert metrics.cause_log_loss(y, p, [1, 1, 1], "default") == pytest.approx(binary)


def test_auc_with_ties_and_weights() -> None:
    assert metrics.auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == pytest.approx(0.75)
    assert metrics.auc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)  # a tie counts half
    # weight 3 on the misranked negative: pairs (pos 0.35 vs neg 0.4) now count three times
    assert metrics.auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8], [1, 3, 1, 1]) == pytest.approx(5 / 8)
    assert math.isnan(metrics.auc([1, 1], [0.2, 0.3]))


def test_calibration_bar() -> None:
    groups = ["2017"] * 1000 + ["2018"] * 10
    y = [1] * 250 + [0] * 750 + [1] * 2 + [0] * 8
    p = np.zeros((1010, 3))
    p[:1000, 1] = 0.30  # predicts 300 against 250 actual: 1.20, inside 0.80-1.25
    p[1000:, 1] = 0.9  # predicts 9 against 2: way off, but too few defaults for the bar
    p[:, 0] = 1 - p[:, 1]
    rows = {r.group: r for r in metrics.calibration(groups, y, p, [1.0] * 1010)}
    assert rows["2017"].ratio == pytest.approx(1.2) and rows["2017"].applies and rows["2017"].passes
    assert not rows["2018"].applies and rows["2018"].passes
    p[:1000, 1] = 0.35  # 350 / 250 = 1.4: fails
    assert not metrics.calibration(groups, y, p, [1.0] * 1010)[0].passes


def test_psi() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, 20000)
    assert metrics.psi(a, rng.normal(0, 1, 20000)) < 0.01
    assert metrics.psi(a, rng.normal(1, 1, 20000)) > 0.25


def test_cumulative_default_competing_risks() -> None:
    pd_ = np.array([[0.1, 0.1, 0.1]])
    pp = np.array([[0.2, 0.2, 0.2]])
    cif = metrics.cumulative_default(pd_, pp)
    # month 1: 0.1; month 2: + 0.7 x 0.1; month 3: + 0.7 x 0.7 x 0.1
    assert np.allclose(cif, [[0.1, 0.17, 0.219]])


def test_horizon_labels_from_the_start_month() -> None:
    # A defaults in month 3; B prepays; C runs the full window; D leaves the data early (censored);
    # E only starts later (not alive at the start)
    ids = ["A"] * 3 + ["B"] * 2 + ["C"] * 3 + ["D"] * 1 + ["E"] * 2
    per = ["201701", "201702", "201703", "201701", "201702", "201701", "201702", "201703", "201701",
           "201702", "201703"]  # fmt: skip
    y = [0, 0, 1, 0, 2, 0, 0, 0, 0, 0, 1]
    assert metrics.horizon_labels(ids, per, y, "201701", 3) == {"A": 1, "B": 0, "C": 0}
    assert metrics._add_months("201711", 2) == "201801"


def test_weighted_sample_measures_match_the_full_table() -> None:
    """The Phase 2 rule: measures on the 1-in-25 sample, weighted, describe the full table."""
    rng = np.random.default_rng(42)
    n = 200_000
    score = rng.uniform(0, 1, n)
    p_default = 0.002 + 0.02 * score
    y = np.where(rng.uniform(size=n) < p_default, 1, 0)
    proba = np.column_stack([1 - p_default, p_default, np.zeros(n)])
    full_ll = metrics.log_loss(y, proba, np.ones(n))
    full_auc = metrics.auc(y == 1, p_default)
    # sample: every event, 1 in 25 non-events, weighted so weighted non-events equal the full count
    keep_none = (y == 0) & (rng.uniform(size=n) < 1 / 25)
    keep = (y == 1) | keep_none
    weight = np.where(y == 0, (y == 0).sum() / keep_none.sum(), 1.0)[keep]
    assert weight[y[keep] == 0].sum() == pytest.approx((y == 0).sum())
    assert metrics.log_loss(y[keep], proba[keep], weight) == pytest.approx(full_ll, rel=0.02)
    assert metrics.auc(y[keep] == 1, p_default[keep], weight) == pytest.approx(full_auc, abs=0.01)
    full_cal = metrics.calibration(["all"] * n, y, proba, np.ones(n))[0]
    sample_cal = metrics.calibration(["all"] * int(keep.sum()), y[keep], proba[keep], weight)[0]
    assert sample_cal.events == full_cal.events  # actual defaults: exactly the same
    assert sample_cal.predicted == pytest.approx(full_cal.predicted, rel=0.02)


def test_auc_matches_the_pairwise_definition() -> None:
    """The vectorised AUC equals the brute-force pairwise count, ties and weights included."""
    rng = np.random.default_rng(3)
    for _ in range(5):
        n = 300
        y = rng.uniform(size=n) < 0.3
        s = np.round(rng.uniform(size=n), 2)  # rounding creates ties
        w = rng.uniform(0.5, 3, n)
        pos, neg = np.where(y)[0], np.where(~y)[0]
        num = sum(
            w[i] * w[j] * (1.0 if s[i] > s[j] else 0.5 if s[i] == s[j] else 0.0) for i in pos for j in neg
        )
        assert metrics.auc(y, s, w) == pytest.approx(num / (w[pos].sum() * w[neg].sum()))


def test_gini() -> None:
    assert metrics.gini(0.5) == 0.0 and metrics.gini(1.0) == 1.0 and metrics.gini(0.75) == pytest.approx(0.5)
    assert math.isnan(metrics.gini(float("nan")))
