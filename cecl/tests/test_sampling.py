from __future__ import annotations

import math

import pytest

from cecl.sampling import Estimate, verdict, weighted_mean, z_score


def test_equal_weights_give_the_usual_mean_and_standard_error():
    x = [1.0, 2.0, 3.0, 4.0]
    e = weighted_mean(x)
    sd = math.sqrt(sum((v - 2.5) ** 2 for v in x) / 3)
    assert e.value == 2.5 and e.n == 4
    assert e.se == pytest.approx(sd / 2)


def test_share_with_equal_weights_matches_sqrt_p_q_over_n():
    x = [1.0] * 30 + [0.0] * 70
    e = weighted_mean(x)
    assert e.value == pytest.approx(0.3)
    assert e.se == pytest.approx(math.sqrt(0.3 * 0.7 / 100) * math.sqrt(100 / 99))


def test_weights_tilt_the_average():
    e = weighted_mean([1.0, 0.0], [3.0, 1.0])
    assert e.value == pytest.approx(0.75)
    assert e.se > 0


def test_single_value_has_no_standard_error():
    e = weighted_mean([5.0])
    assert e.value == 5.0 and math.isnan(e.se)
    assert z_score(e, 5.0) == 0.0
    assert z_score(e, 4.0) == math.inf


@pytest.mark.parametrize(
    ("x", "w", "message"),
    [([], None, "no values"), ([1.0], [1.0, 2.0], "same length"), ([1.0, 2.0], [0.0, 0.0], "more than zero")],
)
def test_bad_input(x, w, message):
    with pytest.raises(ValueError, match=message):
        weighted_mean(x, w)


def test_z_and_verdict():
    e = Estimate(value=0.105, se=0.002, n=50_000)
    assert z_score(e, 0.100) == pytest.approx(2.5)
    assert verdict(1.9) == "agrees"
    assert verdict(-2.5) == "borderline"
    assert verdict(3.2) == "differs"
