"""Model v2 (ADR-009 Amendment 3): the path simulation, the pipeline on made-up books, the selection rule.

Needs requirements-ml.txt (skipped without it)."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
pytest.importorskip("lightgbm")

from cecl import features as fx  # noqa: E402
from cecl.models import multistate, simulate  # noqa: E402
from cecl.models import v2_pipeline as v2  # noqa: E402
from cecl.tests.test_transitions import MATRIX, Fixed, macro, start_table  # noqa: E402

V2_MONTHS = [f"{y}{m:02d}" for y in range(2005, 2020) for m in range(1, 13)] + [
    f"{y}{m:02d}" for y in (2022, 2023, 2024) for m in range(1, 13) if "202207" <= f"{y}{m:02d}" <= "202406"
]


def v2_start(states: list[int], hist: list[list[int]] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = start_table(states)
    for f in fx.V2_STORED:
        if f not in start:
            start[f] = 0.0
    start["forbearance_prev"], start["paused_12"] = 0.0, 0.0
    n = len(states)
    history = pd.DataFrame({"loan_id": start["loan_id"]})
    for j in range(1, 13):
        history[f"state_hist_{j}"] = [h[j - 1] if hist else 0 for h in (hist or [[0] * 12] * n)]
        history[f"pause_hist_{j}"] = 0.0
    history["state_hist_1"] = states
    return start, history


class Recorder(Fixed):
    """Fixed probabilities; remembers the inputs it was given."""

    def __init__(self, row: list[float]) -> None:
        super().__init__(row)
        self.features = fx.v2_features()
        self.seen: list[dict[str, np.ndarray]] = []

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        self.seen.append(X)
        return super().predict_proba(X)


def test_simulation_matches_the_matrix_on_average() -> None:
    models = {s: Recorder(r) for s, r in MATRIX.items()}
    start, history = v2_start([fx.CURRENT, fx.LATE_60] * 200)
    proj = simulate.project(models, start, history, macro(), "201701", 4, paths=50, seed=1)
    T = np.array([MATRIX[s] for s in fx.LIVE_STATES])
    for i, s in enumerate([fx.CURRENT, fx.LATE_60]):
        v = np.zeros(3)
        v[s] = 1.0
        for k in range(4):
            step = v @ T
            got = proj.p_default[i::2, k].mean()
            assert got == pytest.approx(step[fx.DEFAULTED], abs=0.01)
            v = step[:3]
    # month 0 is exact (the paths have not moved yet)
    assert proj.p_default[1, 0] == pytest.approx(0.60) and proj.p_prepay[0, 0] == pytest.approx(0.05)
    again = simulate.project(models, start, history, macro(), "201701", 4, paths=50, seed=1)
    assert np.array_equal(proj.p_default, again.p_default)  # fixed seed: the same projection


def test_history_inputs_are_exact_on_each_path() -> None:
    # a loan 60 days late, 30 days late the month before, then current: the 30-day model sends it to
    # current for sure, so its path is known: month 0 in 60, month 1 current (cured), ...
    models = {fx.CURRENT: Recorder([1.0, 0, 0, 0, 0]), fx.LATE_30: Recorder([1.0, 0, 0, 0, 0]),
              fx.LATE_60: Recorder([1.0, 0, 0, 0, 0])}  # fmt: skip
    start, history = v2_start([fx.LATE_60], [[2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]])
    start["months_since_first_miss"] = 2.0
    start["late_months_12"], start["state_last_month"], start["worst_state_6"] = 2.0, 1.0, 1.0
    assert all(v == 0 for v in simulate.check_history(start, history).values())
    simulate.project(models, start, history, macro(), "201701", 3, paths=1)
    month1 = models[fx.CURRENT].seen[0]  # first month scored as current (k = 1)
    assert month1["state_last_month"][0] == 2 and month1["late_months_12"][0] == 2
    assert (
        month1["worst_state_6"][0] == 2 and month1["months_since_first_miss"][0] == 3
    )  # t-7..t-2 holds the 60
    month2 = models[fx.CURRENT].seen[1]
    assert month2["state_last_month"][0] == 0 and month2["worst_state_6"][0] == 2
    assert month2["months_since_first_miss"][0] == 4 and month2["ever_missed"][0] == 1


def test_first_miss_counts_from_a_projected_miss() -> None:
    models = {fx.CURRENT: Recorder([0, 1.0, 0, 0, 0]), fx.LATE_30: Recorder([1.0, 0, 0, 0, 0]),
              fx.LATE_60: Recorder([1.0, 0, 0, 0, 0])}  # fmt: skip
    start, history = v2_start([fx.CURRENT])
    simulate.project(models, start, history, macro(), "201701", 3, paths=1)
    # month 0 current -> 30 (row of month 0 is late); month 1 scored in 30: first miss 1 month ago
    assert models[fx.LATE_30].seen[0]["months_since_first_miss"][0] == 1
    assert models[fx.CURRENT].seen[1]["months_since_first_miss"][0] == 2


def test_v1_history_from_v2() -> None:
    _, history = v2_start([fx.LATE_30], [[1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]])
    out = simulate.v1_history(history)
    assert (
        out.loc[0, "late_hist_1"] == 1 and out.loc[0, "late_hist_2"] == 1 and out.loc[0, "late_hist_3"] == 0
    )
    assert out.loc[0, "late_hist_12"] == 1 and len(multistate.HISTORY) == 12


def v2_book(start: int, n: int = 5000, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed + start)
    frame = pd.DataFrame(
        {f: rng.uniform(0, 1, n) for f in fx.V2_STORED + ("cpi_inflation_12", "card_delinquency")}
    )
    frame["current_ltv"] = rng.uniform(40, 130, n)
    frame["rate_incentive"] = rng.normal(-0.5, 1.5, n)
    frame["loan_age"] = rng.uniform(0, 200, n)
    frame["months_since_first_miss"] = np.where(rng.uniform(size=n) < 0.2, rng.integers(1, 40, n), 0)
    frame["worst_state_6"] = rng.integers(0, 3, n)
    risk = (
        0.05 * (frame["current_ltv"] - 80)
        + 0.8 * (frame["rate_incentive"] <= -1)
        + 0.5 * frame["worst_state_6"]
    )
    logits = np.zeros((n, 5))
    logits[:, fx.PREPAID] = frame["rate_incentive"] - 3.5
    logits[:, fx.LATE_30] = risk - 3.0 if start == fx.CURRENT else -1.0
    logits[:, fx.LATE_60] = -9.0 if start == fx.CURRENT else risk - 1.0
    logits[:, fx.DEFAULTED] = -9.0 if start == fx.CURRENT else risk - 2.0
    logits[:, fx.CURRENT] = 0.0 if start == fx.CURRENT else -risk
    logits[:, start] = 0.0
    p = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    frame["state"] = (rng.uniform(size=n)[:, None] > np.cumsum(p, axis=1)).sum(axis=1)
    frame["state_prev"] = start
    frame["weight"] = 1.0
    frame["loan_id"] = [f"L{i % 900:04d}" for i in range(n)]
    frame["period_ym"] = [V2_MONTHS[i % len(V2_MONTHS)] for i in range(n)]
    frame["vintage_year"] = 2003 + (np.arange(n) % 6)
    return frame


def test_tune_exams_lock_bar_and_info_exam() -> None:
    feats = fx.v2_features()
    frame = v2.add_derived(v2_book(fx.CURRENT), feats)
    record, models = v2.tune("logistic", fx.CURRENT, frame, feats)
    assert len(record["scores"]) == 2 and all(len(s) == 4 for s in record["scores"]) and len(models) == 4
    exams = v2.exam_results(models, frame)
    assert [e["exam"] for e in exams] == list(fx.V2_FOLD_NAMES)
    final = v2.fit("logistic", fx.CURRENT, frame, record["chosen"], feats)
    lock = v2.lock_band_bar(final, frame)
    assert lock["in_sample"] and set(lock["cells"]) == {"30", "60", "default", "prepaid"}
    info_model, info = v2.info_exam("logistic", fx.CURRENT, frame, record["chosen"], feats)
    assert info["rows"] == int(((frame.period_ym >= "202207") & (frame.period_ym <= "202406")).sum())
    assert list(v2.lock_band([-3, -1.5, -0.5, 0.5, 2.0])) == [
        "2+ below",
        "1-2 below",
        "0-1 below",
        "0-1 above",
        "1+ above",
    ]


def test_stress_screen_keeps_only_eligible_helpful_inputs() -> None:
    frame = v2.add_derived(v2_book(fx.CURRENT, 4000), fx.v2_features())
    rec = v2.screen_stress(frame, ("cpi_inflation_12", "card_delinquency"))
    assert set(rec["candidates"]) == {"cpi_inflation_12", "card_delinquency"}
    assert rec["candidates"]["card_delinquency"]["kept"] is False  # not eligible (no Fed path)
    assert rec["candidates"]["cpi_inflation_12"]["kept"] is False  # pure noise here: no gain


def _result(avg: float, bars: bool = True, reasons: bool = True) -> dict:
    exam = {"exam": "F1", "start_state": "current", "calibration_passes": bars, "calibration": {},
            "moves": {}}  # fmt: skip
    return {
        "average": avg,
        "exams": [exam] * 4,
        "lock_band": {"passes": True, "cells": {}},
        "reason_codes": reasons,
    }


def test_two_percent_rule_and_ties_to_logistic() -> None:
    assert v2.select_step({"logistic": _result(1.0), "lightgbm": _result(0.97)})["winner"] == "lightgbm"
    assert v2.select_step({"logistic": _result(1.0), "lightgbm": _result(0.985)})["winner"] == "logistic"
    assert (
        v2.select_step({"logistic": _result(1.0), "lightgbm": _result(0.9, bars=False)})["winner"]
        == "logistic"
    )
    assert (
        v2.select_step({"logistic": _result(1.0), "lightgbm": _result(0.9, reasons=False)})["winner"]
        == "logistic"
    )
    assert v2.FAMILIES == ("logistic", "lightgbm")  # EBM dropped for v2


def test_walk_forward_verdicts() -> None:
    def chk(actual: float, projected: float, years: list[tuple[str, float, float]]) -> multistate.Check:
        c = multistate.Check(totals={"default_actual": actual, "default_projected": projected})
        c.cells = [multistate._cell("default", "year", y, a, p) for y, a, p in years]
        return c

    good = chk(
        1000, 950, [("2011", 400, 380), ("2012", 300, 290), ("2013", 150, 300)]
    )  # 2013: under 200, not judged
    assert v2.exam_verdict(good)["passes"]
    bad = chk(1000, 950, [("2011", 400, 300), ("2012", 600, 650)])
    assert v2.exam_verdict(bad)["failing"] == ["year 2011 0.75"]
    assert not v2.exam_verdict(chk(1000, 700, []))["passes"]
    assert v2.crisis_verdict(chk(1000, 700, []), chk(1000, 500, []))["passes"]
    assert not v2.crisis_verdict(chk(1000, 1400, []), chk(1000, 1300, []))["passes"]
