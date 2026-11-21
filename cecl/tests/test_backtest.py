"""Phase 6 backtest pieces, on made-up numbers."""

from __future__ import annotations

import math

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from cecl import backtest as bt  # noqa: E402
from cecl.models import multistate  # noqa: E402


def toy_macro() -> multistate.Macro:
    months = [multistate.add_months("198912", k) for k in range(1, 12 * 30 + 1)]  # 1990-01 .. 2019-12
    idx = pd.Index(months)
    hpi = pd.DataFrame({"AA": 100 * np.exp(0.003 * np.arange(len(months))), "BB": 100.0}, index=idx)
    unemp = pd.DataFrame({"AA": np.where(np.arange(len(months)) < 299, 5.0, 8.0), "BB": 4.0}, index=idx)
    rate = pd.Series(6.0, index=idx)
    return multistate.Macro(hpi, unemp, rate)


def test_flat_macro_uses_only_data_to_the_date() -> None:
    actual = toy_macro()
    f = bt.flat_macro(actual, "201412", 48)
    months = [multistate.add_months("201412", k) for k in range(1, 49)]
    # unemployment held at its December 2014 value for 24 months, then reverts to the 1990-2014 mean
    assert f.unemployment.loc[months[23], "AA"] == pytest.approx(8.0)
    lr = actual.unemployment.loc["199001":"201412", "AA"].mean()
    assert f.unemployment.loc[months[35], "AA"] == pytest.approx(lr)
    # house prices keep their trailing 12-month growth during the forecast
    assert np.log(f.hpi.loc[months[11], "AA"] / f.hpi.loc["201412", "AA"]) == pytest.approx(0.036)
    # nothing after the date is read: changing the future changes nothing
    later = toy_macro()
    later.unemployment.loc["201501":, "AA"] = 99.0
    assert bt.flat_macro(later, "201412", 48).unemployment.equals(f.unemployment)
    assert bt.long_run_growth(actual, "201412")["AA"] == pytest.approx(0.036, rel=1e-3)


def test_counted_to_censoring() -> None:
    p = np.full((2, 4), 0.1)
    assert bt.counted_to_censoring(p, np.array([4, 2])) == pytest.approx([0.4, 0.2])


def test_crisis_verdict() -> None:
    v = bt.crisis_verdict((45, 100), (30, 100), (40, 100), (20, 100))
    assert v["defaults_ratio"] == pytest.approx(0.45) and not v["defaults_in_bar"] and not v["passes"]
    assert v["closer_than_benchmark"]  # 0.45 vs 0.40 and 0.30 vs 0.20
    assert bt.crisis_verdict((95, 100), (110, 100), (60, 100), (50, 100))["passes"]


def test_rolling_verdict() -> None:
    dates = {f"20{12 + i}12": (100.0 * (1.2 if i < 9 else 3.0), 100.0) for i in range(11)}
    v = bt.rolling_verdict(dates)
    assert v["dates_in_band"] == 9 and v["dates_needed"] == 8
    assert v["pooled_ratio"] == pytest.approx((9 * 1.2 + 2 * 3.0) / 11) and not v["pooled_in_bar"]
    assert not v["passes"]
    assert bt.rolling_verdict({d: (100.0, 100.0) for d in dates})["passes"]


def test_forbearance_verdict() -> None:
    v = bt.forbearance_verdict((90, 100), (140, 100), extra_defaults=1000, extra_cured_12m=800)
    assert v["passes"] and v["cured_share"] == pytest.approx(0.8)
    assert not bt.forbearance_verdict((90, 100), (140, 100), 1000, 300)["passes"]
    assert math.isnan(bt.forbearance_verdict((1, 1), (1, 1), 0, 0)["cured_share"])


def test_yearly() -> None:
    assert bt.yearly(np.array([1.0, 2.0, 3.0]), ["200711", "200712", "200801"]) == {"2007": 3.0, "2008": 3.0}
