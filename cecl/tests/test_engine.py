"""The CECL engine and the scenario builder, on made-up books and economies."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from cecl import engine, scenarios  # noqa: E402
from cecl.models import multistate  # noqa: E402


class FlatLGD:
    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), 0.25)


def book(n: int = 2, remaining: float = 120.0) -> pd.DataFrame:
    return pd.DataFrame({"loan_id": [f"L{i}" for i in range(n)], "property_state": ["TX"] * n,
                         "upb_start": [100000.0] * n, "rate_start": [0.0] * n, "remaining_start": [remaining] * n,
                         "loan_age": [24.0] * n, "orig_upb": [120000.0] * n, "orig_ltv": [80.0] * n,
                         "hpi_orig": [100.0] * n, "mi_pct": [0.0] * n, "credit_score": [700.0] * n,
                         "occupancy_investor": [0.0] * n})  # fmt: skip


def flat_macro(first: str = "201901", months: int = 120) -> multistate.Macro:
    idx = [multistate.add_months(first, k) for k in range(-24, months)]
    return multistate.Macro(pd.DataFrame({"TX": 110.0}, index=idx), pd.DataFrame({"TX": 4.0}, index=idx),
                            pd.Series(4.0, index=idx))  # fmt: skip


def test_monthly_part_is_pd_times_ead_times_lgd() -> None:
    b = book(1, remaining=12.0)  # matures inside the monthly part: no tail
    p_def, p_pre = np.full((1, 36), 0.001), np.zeros((1, 36))
    out = engine.price(b, p_def, p_pre, flat_macro(), "201812", FlatLGD(), {}, {"US": 0.0})
    bal = [
        multistate.scheduled_balance(np.array([1e5]), np.array([0.0]), np.array([12.0]), k)[0]
        for k in range(1, 37)
    ]
    assert out["total"][0] == pytest.approx(sum(0.001 * x * 0.25 for x in bal))
    assert out["tail"][0] == 0.0 and out["forecast"][0] + out["reversion"][0] == pytest.approx(
        out["total"][0]
    )


def test_tail_uses_month_36_hazards_until_maturity() -> None:
    b = book(1, remaining=36.0 + 24.0)  # two tail years
    h = 0.002
    p_def = np.array([[h * (1 - h) ** (k - 1) for k in range(1, 37)]])  # constant hazard, no payoffs
    out = engine.price(b, p_def, np.zeros((1, 36)), flat_macro(), "201812", FlatLGD(), {}, {"US": 0.0})
    s36 = (1 - h) ** 36
    year = 1 - (1 - h) ** 12
    assert out["lifetime_pd"][0] == pytest.approx(
        p_def.sum() + s36 * year + s36 * (1 - year) * year, rel=1e-6
    )
    surv, hd, hp = engine.tail_hazards(p_def, np.zeros((1, 36)))
    assert hd[0] == pytest.approx(h) and hp[0] == 0.0 and surv[0] == pytest.approx(s36)


def test_scenario_b4b_reproduces_the_national_change() -> None:
    months = pd.date_range("1990-01-01", "2026-06-01", freq="MS").date
    rng = np.random.default_rng(1)
    nat_u = 5 + np.cumsum(rng.normal(0, 0.1, len(months)))
    nat_h = 100 * np.exp(np.cumsum(rng.normal(0.003, 0.01, len(months))))
    rows, nrows = [], []
    for i, m in enumerate(months):
        nrows += [{"month": m, "variable": "unemployment_rate", "value": nat_u[i]},
                  {"month": m, "variable": "hpi", "value": nat_h[i]},
                  {"month": m, "variable": "mortgage_rate_30y", "value": 6.0}]  # fmt: skip
        for st, b in (("AA", 0.5), ("BB", 2.0)):
            rows += [{"state": st, "month": m, "variable": "unemployment_rate", "value": 5 + b * (nat_u[i] - 5)},
                     {"state": st, "month": m, "variable": "hpi", "value": 100 * (nat_h[i] / 100) ** b}]  # fmt: skip
    fed = pd.DataFrame([{"scenario": "baseline", "horizon": h, "variable": v, "fed_change": c}
                        for h in range(1, 14) for v, c in (("unemployment_rate", 0.2 * h), ("hpi", 1 - 0.02 * h),
                                                         ("mortgage_rate_30y", 0.0))])  # fmt: skip
    lr = pd.DataFrame([{"geo": g, "variable": v, "measure": "x", "value": val} for g in ("US", "AA", "BB")
                       for v, val in (("unemployment_rate", 5.0), ("hpi", 0.03), ("mortgage_rate_30y", 6.0))])  # fmt: skip
    w = {"AA": 3.0, "BB": 1.0}
    sc = scenarios.build("baseline", "202606", pd.DataFrame(rows), pd.DataFrame(nrows), fed, lr, w, 48)
    assert sc.betas["hpi"]["BB"] > sc.betas["hpi"]["AA"]
    nat = scenarios.national_changes(fed, "baseline", "unemployment_rate")
    assert scenarios.weighted_change(sc, w, "unemployment_rate", 24) == pytest.approx(nat[24])
    assert scenarios.weighted_change(sc, w, "hpi", 12) == pytest.approx(
        scenarios.national_changes(fed, "baseline", "hpi")[12]
    )
    assert sc.macro.unemployment.loc[sc.months[35], "AA"] == pytest.approx(
        5.0
    )  # reverted to its long-run level
    assert sc.macro.mortgage_rate[sc.months[40]] == pytest.approx(6.0)


def test_first_state_starts_paths_from_the_known_state() -> None:
    from cecl import features as fx
    from cecl.models import simulate
    from cecl.tests.test_transitions import MATRIX, macro
    from cecl.tests.test_v2 import Recorder, v2_start

    models = {s: Recorder(r) for s, r in MATRIX.items()}
    start, history = v2_start([fx.CURRENT] * 4)
    proj = simulate.project(models, start, history, macro(), "201701", 3, paths=2,
                            first_state=np.array([2, 2, 0, 0]), first_pause=np.array([1, 0, 0, 0]))  # fmt: skip
    assert proj.p_default[:, 0].sum() == 0.0  # month 0 is known, not projected
    # loans known to be 60 days late default in month 1 with the 60-day model's chance
    assert proj.p_default[0, 1] == pytest.approx(MATRIX[fx.LATE_60][fx.DEFAULTED])
    assert proj.p_default[2, 1] == pytest.approx(MATRIX[fx.CURRENT][fx.DEFAULTED])
    seen = models[fx.LATE_60].seen[0]
    assert seen["forbearance_prev"][0] == 1.0  # the known pause is in the history
