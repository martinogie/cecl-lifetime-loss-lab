from __future__ import annotations

import pytest

from cecl import features as fx


@pytest.mark.parametrize(
    ("period", "split"),
    [("199901", "train"), ("201612", "train"), ("201701", "validate"), ("201912", "validate"),
     ("202001", "covid"), ("202206", "covid"), ("202207", "test"), ("202606", "test")],
)  # fmt: skip
def test_split_follows_adr_009(period: str, split: str) -> None:
    assert fx.split_of(period) == split


def test_split_outside_every_window() -> None:
    with pytest.raises(ValueError, match="outside every split"):
        fx.split_of("202607")
    assert "when period_ym between '202207' and '202606' then 'test'" in fx.split_sql()
    assert fx.HOLDOUT == ("test",)


def test_outcomes() -> None:
    assert [fx.outcome_of(s) for s in ("current", "delinquent", "defaulted", "prepaid")] == [0, 0, 1, 2]
    with pytest.raises(ValueError, match="censored"):
        fx.outcome_of("censored")  # left out, never "no event"


def test_current_ltv_and_rate_incentive() -> None:
    # bought for 250,000 with 200,000 (LTV 80); prices up 25%; balance now 180,000 -> 57.6%
    assert fx.current_ltv(180_000, 200_000, 80, 125.0, 100.0) == pytest.approx(57.6)
    # prices down 20%: the same balance is now 90% of the value
    assert fx.current_ltv(180_000, 200_000, 80, 80.0, 100.0) == pytest.approx(90.0)
    assert fx.current_ltv(None, 200_000, 80, 80.0, 100.0) is None
    assert fx.rate_incentive(6.5, 5.0) == pytest.approx(1.5) and fx.rate_incentive(6.5, None) is None


def test_feature_definitions_are_consistent() -> None:
    assert len(set(fx.FEATURE_NAMES)) == len(fx.FEATURE_NAMES)
    assert set(fx.RANGES) <= set(fx.FEATURE_NAMES) | set(fx.TRANSITION_FEATURES) | {
        f.name for f in fx.V2_ALL_EXTRA
    }
    directions = {f.name: (f.default_direction, f.prepay_direction) for f in fx.FEATURES}
    assert directions["credit_score"] == (-1, 0) and directions["current_ltv"] == (1, 0)
    assert directions["rate_incentive"] == (0, 1)
    assert all(d in (-1, 0, 1) for pair in directions.values() for d in pair)
    assert fx.SAMPLE_EVERY == 25 and fx.SAMPLE_SEED
