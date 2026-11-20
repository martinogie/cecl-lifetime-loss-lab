"""Phase 5 pieces: shocks, payoff multiplier, LGD floor, quarterly sums, run definitions."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from cecl import engine, lgd, scenarios, stress  # noqa: E402
from cecl import features as fx  # noqa: E402
from cecl.models import simulate  # noqa: E402
from cecl.tests.test_engine import toy_economy  # noqa: E402


def test_shock_ramps_holds_then_reverts() -> None:
    state, national, fed, lr, w = toy_economy()
    base = scenarios.build("baseline", "202606", state, national, fed, lr, w, 48)
    shock = scenarios.build("baseline", "202606", state, national, fed, lr, w, 48,
                            shock=scenarios.Shock(hpi_pct=-0.30, unemployment_pts=4.0, rate_pts=2.0))  # fmt: skip
    m = base.months
    ratio = shock.macro.hpi["AA"] / base.macro.hpi["AA"]
    assert ratio[m[5]] == pytest.approx(0.70**0.5)  # half way (in log terms) after 6 months
    assert ratio[m[11]] == pytest.approx(0.70)  # fully in place after 4 quarters
    assert ratio[m[23]] == pytest.approx(0.70)  # held to the end of the forecast
    du = shock.macro.unemployment["AA"] - base.macro.unemployment["AA"]
    assert du[m[2]] == pytest.approx(1.0) and du[m[23]] == pytest.approx(4.0)
    assert shock.macro.unemployment.loc[m[35], "AA"] == pytest.approx(5.0)  # reverted to the long-run level
    assert (shock.macro.mortgage_rate - base.macro.mortgage_rate)[m[23]] == pytest.approx(2.0)
    jump = scenarios.build(
        "baseline", "202606", state, national, fed, lr, w, 48, shock=scenarios.SHOCKS["hpi_m30_immediate"]
    )
    assert (jump.macro.hpi["AA"] / base.macro.hpi["AA"])[m[0]] == pytest.approx(0.70)  # in place in month 1


def test_payoff_multiplier_keeps_rows_summing_to_one() -> None:
    P = np.array([[0.90, 0.04, 0.0, 0.01, 0.05], [0.50, 0.20, 0.10, 0.10, 0.10]])
    for m in (0.8, 1.2):
        Q = simulate.scale_payoff(P, m)
        assert Q.sum(axis=1) == pytest.approx(1.0)
        assert Q[:, fx.PREPAID] == pytest.approx(P[:, fx.PREPAID] * m)
        others = [c for c in range(5) if c != fx.PREPAID]
        assert (Q[:, others] / P[:, others].clip(1e-12))[
            P[:, others] > 0
        ].std() < 0.2  # proportional within a row
    assert simulate.scale_payoff(P, 1.0) == pytest.approx(P)


def test_lgd_floor_applies_to_severity_only() -> None:
    class Toy:
        def p_loss(self, X: np.ndarray) -> np.ndarray:
            return np.array([0.5, 0.2])

        def severity(self, X: np.ndarray) -> np.ndarray:
            return np.array([0.02, 0.30])

    f = lgd.FlooredLGD(Toy(), 0.10)
    assert f.predict(np.zeros((2, 1))) == pytest.approx([0.5 * 0.10, 0.2 * 0.30])


def test_quarterly_sums() -> None:
    el = np.arange(2 * 36, dtype=float).reshape(2, 36)
    q = engine.quarters(el, el, el)
    assert q["q_el"].shape == (2, 12)
    assert q["q_el"][0, 0] == pytest.approx(0 + 1 + 2) and q["q_el"].sum() == pytest.approx(el.sum())


def test_variants_are_valid_and_cover_the_decided_weights() -> None:
    stress.check()
    for w in ("80/15/5", "60/30/10 (booked)", "50/35/15", "40/40/20"):
        assert w in stress.WEIGHT_SETS
    assert stress.VARIANTS["hpi_m30_immediate"].scenarios == ("baseline",)
    assert stress.VARIANTS["payoff_12"].payoff_multiplier == 1.2
    assert (
        stress.tag("202606", "202606", "baseline") == "202606_202606_baseline"
    )  # booked keeps Phase 4 names
    assert stress.tag("202606", "202606", "baseline", "hpi_m10") == "202606_202606_baseline__hpi_m10"
