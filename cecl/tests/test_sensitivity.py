"""The downturn-sensitivity check (ADR-009 Amendment 4): scenario paths, the tie-out, per-group R and the
pass tests, on made-up books. Needs requirements-ml.txt (skipped without it)."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from cecl import features as fx  # noqa: E402
from cecl.models import sensitivity as sv  # noqa: E402
from cecl.models import simulate  # noqa: E402
from cecl.tests.test_transitions import macro  # noqa: E402
from cecl.tests.test_v2 import v2_start  # noqa: E402

S = "201701"  # the made-up economy covers 2015-2020


def shock(name: str) -> sv.Shock:
    return next(s for s in sv.ALL if s.name == name)


def test_ramp_builds_over_twelve_months_then_holds() -> None:
    assert sv.ramp("201701", S) == pytest.approx(1 / 12)
    assert sv.ramp("201712", S) == 1.0 and sv.ramp("201906", S) == 1.0
    assert sv.ramp("201612", S) == 0.0


def test_scenarios_hold_the_economy_and_apply_the_shock() -> None:
    m = macro()
    m.hpi.loc["201612"] = 120.0  # the level the scenario holds
    base = sv.scenario(m, sv.BASELINE, S, 24)
    hit = sv.scenario(m, shock("combined"), S, 24)
    assert base.hpi.loc["201706", "TX"] == 120.0 and base.unemployment.loc["201901", "TX"] == 4.0
    assert hit.hpi.loc["201701", "TX"] == pytest.approx(120.0 * (1 - 0.30 / 12))
    assert hit.hpi.loc["201712", "TX"] == pytest.approx(84.0) and hit.hpi.loc[
        "201812", "TX"
    ] == pytest.approx(84.0)
    assert hit.unemployment.loc["201712", "TX"] == pytest.approx(8.0)
    assert hit.hpi.loc["201606", "TX"] == m.hpi.loc["201606", "TX"]  # history before the start untouched
    assert hit.mortgage_rate.loc["201812"] == m.mortgage_rate.loc["201612"]


def test_tie_out_is_exact_and_catches_a_wrong_path() -> None:
    m = macro()
    start, _ = v2_start([fx.CURRENT] * 3)
    base, hit = sv.scenario(m, sv.BASELINE, S, 24), sv.scenario(m, shock("combined"), S, 24)
    err = sv.tie_out(start, base, hit, shock("combined"), S, 24)
    assert max(err.values()) <= sv.TOLERANCE
    hit.unemployment.loc["201803", "TX"] += 0.5
    assert sv.tie_out(start, base, hit, shock("combined"), S, 24)["unemployment_max_error"] == pytest.approx(
        0.5
    )


def test_ltv_after_a_house_price_fall_is_higher() -> None:
    m = macro()
    start, _ = v2_start([fx.CURRENT])
    base = sv.ltv_after(start, sv.scenario(m, sv.BASELINE, S, 24), S)
    hit = sv.ltv_after(start, sv.scenario(m, shock("hpi_-30"), S, 24), S)
    assert hit[0] == pytest.approx(base[0] / 0.70)


class Stress:
    """Default chance rises with unemployment and current LTV (to check the whole check end to end)."""

    def __init__(self) -> None:
        self.features = fx.v2_features()

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        n = len(X["unemployment"])
        d = np.clip(0.002 * (1 + 0.3 * (X["unemployment"] - 4.0)) * (np.nan_to_num(X["current_ltv"], nan=60.0) / 60.0) ** 2,
                    1e-5, 0.2)  # fmt: skip
        p = np.zeros((n, 5))
        p[:, fx.DEFAULTED], p[:, fx.PREPAID] = d, 0.01
        p[:, fx.CURRENT] = 1 - d - 0.01
        return p


def test_end_to_end_a_stress_sensitive_model_passes() -> None:
    m = macro()
    start, history = v2_start([fx.CURRENT] * 40)
    start["credit_score"] = [650.0, 760.0] * 20
    start["orig_ltv"] = [95.0] * 20 + [60.0] * 20
    models = {s: Stress() for s in fx.LIVE_STATES}
    defaults, ltv = {}, {}
    for sh in sv.ALL:
        mac = sv.scenario(m, sh, S, 24)
        defaults[sh.name] = simulate.project(
            models, start, history, mac, S, 24, paths=2, seed=7
        ).p_default.sum(axis=1)
        if sh.name in sv.ORDERING_SHOCKS:
            ltv[sh.name] = sv.ltv_after(start, mac, S)
    r = sv.ratios(defaults, sv.groups(start, ltv))
    base, hit = sv.scenario(m, sv.BASELINE, S, 24), sv.scenario(m, shock("hpi_-30"), S, 24)
    mech = {"history_check": simulate.check_history(start, history),
            "tie_out": {"hpi_-30": sv.tie_out(start, base, hit, shock("hpi_-30"), S, 24)}}  # fmt: skip
    out = sv.evaluate(r, mech)
    assert out["tests"]["1_monotonic"]["passes"] and out["tests"]["6_combined"]["passes"]
    assert r["R"]["combined"] > r["R"]["hpi_-30"] > r["R"]["hpi_-20"] > 1.0
    assert set(r["groups"]["state"]) == {"TX"} and r["groups"]["vintage"]["2015"]["loans"] == 40


def _ratios(R: dict[str, float], hi: float = 2.0, lo: float = 1.5) -> dict:
    g = {"R": {s: hi for s in R}}
    l_ = {"R": {s: lo for s in R}}
    groups = {"credit_score": {"below_680": g, "740_plus": l_}}
    for s in sv.ORDERING_SHOCKS:
        groups[f"ltv_after_{s}"] = {"above_80": g, "80_or_below": l_}
    return {"R": R, "groups": groups}


GOOD = {
    "baseline": 1.0,
    "hpi_-10": 1.05,
    "hpi_-20": 1.15,
    "hpi_-30": 1.30,
    "ur_+2": 1.12,
    "ur_+4": 1.30,
    "combined": 1.80,
}
MECH = {"history_check": {"starting_state": 0}, "tie_out": {"x": {"hpi_max_error": 0.0}}}


def test_evaluate_passes_and_each_test_can_fail() -> None:
    assert sv.evaluate(_ratios(GOOD), MECH)["passes"]
    cases = {
        "1_monotonic": GOOD | {"hpi_-10": 1.01},
        "2_material_house_prices": GOOD | {"hpi_-30": 1.20},
        "3_material_unemployment": GOOD | {"ur_+2": 1.08},
        "4_plausible": GOOD | {"combined": 11.0},
        "6_combined": GOOD | {"combined": 1.29},
    }
    for test, R in cases.items():
        out = sv.evaluate(_ratios(R), MECH)
        assert not out["tests"][test]["passes"] and not out["passes"], test
    assert not sv.evaluate(_ratios(GOOD), MECH | {"history_check": {"starting_state": 3}})["tests"][
        "5_mechanics"
    ]["passes"]
    out = sv.evaluate(_ratios(GOOD, hi=1.2, lo=1.4), MECH)
    assert not out["tests"]["7_risk_ordering"]["passes"]
    # the combined shock must beat both single shocks, not only the floor
    assert not sv.evaluate(_ratios(GOOD | {"ur_+4": 1.9}), MECH)["tests"]["6_combined"]["passes"]


def _cell(loans: int, base: float, R: float) -> dict:
    return {"loans": loans, "baseline_defaults": base, "R": {s: R for s in ("hpi_-30", "ur_+4")}}


def test_amendment5_points_can_pass_where_ratios_fail() -> None:
    # riskier: 10% -> 15% (R 1.5, +5 points); safer: 1% -> 2% (R 2.0, +1 point)
    risky, safe = _cell(1000, 100.0, 1.5), _cell(1000, 10.0, 2.0)
    groups = {"credit_score": {"below_680": risky, "740_plus": safe}}
    for s in sv.ORDERING_SHOCKS:
        groups[f"ltv_after_{s}"] = {"above_80": risky, "80_or_below": safe}
    out = sv.risk_ordering_points(groups)
    assert out["passes"]
    assert out["shocks"]["hpi_-30"]["score"]["riskier"]["rise_points"] == pytest.approx(5.0)
    assert out["shocks"]["ur_+4"]["ltv"]["safer"]["rise_points"] == pytest.approx(1.0)
    groups["credit_score"]["below_680"] = _cell(1000, 100.0, 1.05)  # +0.5 points: below the safer group's +1
    assert not sv.risk_ordering_points(groups)["passes"]
    saved = {"verdict": sv.evaluate(_ratios(GOOD, hi=1.2, lo=1.4), MECH), "ratios": {"groups": groups}}
    again = sv.reevaluate(saved)
    assert "7_risk_ordering" not in again["tests"] and not again["passes"]
    assert again["original_verdict"]["tests"]["7_risk_ordering"]["passes"] is False
