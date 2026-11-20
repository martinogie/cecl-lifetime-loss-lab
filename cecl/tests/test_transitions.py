"""The multi-state transition models, walk-forward tuning, evaluation and projection (ADR-011).

Small made-up books; needs requirements-ml.txt (skipped without it)."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
pytest.importorskip("lightgbm")
pytest.importorskip("interpret")

from cecl import features as fx  # noqa: E402
from cecl.models import multistate, transition_pipeline, transitions  # noqa: E402

MONTHS = [f"{y}{m:02d}" for y in range(2005, 2020) for m in range(1, 13)]


def book(start: int, n: int = 6000, seed: int = 3) -> pd.DataFrame:
    """Loan-months starting in `start`: rolls rise with LTV, cures fall with it, prepayment rises with
    the rate incentive."""
    rng = np.random.default_rng(seed + start)
    frame = pd.DataFrame({f: rng.uniform(0, 1, n) for f in fx.TRANSITION_FEATURES})
    frame["current_ltv"] = rng.uniform(40, 130, n)
    frame["credit_score"] = rng.uniform(600, 820, n)
    frame["rate_incentive"] = rng.normal(0, 1.5, n)
    risk = 0.05 * (frame["current_ltv"] - 80) - 0.01 * (frame["credit_score"] - 720)
    logits = np.zeros((n, 5))
    logits[:, fx.PREPAID] = frame["rate_incentive"] - 3.5
    if start == fx.CURRENT:
        logits[:, fx.LATE_30] = risk - 3.0
        logits[:, fx.LATE_60] = -9.0
        logits[:, fx.DEFAULTED] = -9.0
    else:
        logits[:, fx.CURRENT] = -risk - 0.5
        logits[:, fx.LATE_30] = -1.0 if start == fx.LATE_60 else 0.0
        logits[:, fx.LATE_60] = (risk - 1.0) if start == fx.LATE_30 else 0.0
        logits[:, fx.DEFAULTED] = (risk - 0.5) if start == fx.LATE_60 else -6.0
        logits[:, start] = 0.0
    p = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    u = rng.uniform(size=n)[:, None]
    frame["state"] = (u > np.cumsum(p, axis=1)).sum(axis=1)
    frame["state_prev"] = start
    frame["weight"] = np.where(frame["state"] == start, 2.0, 1.0)
    frame["loan_id"] = [f"L{i % 900:04d}" for i in range(n)]
    frame["period_ym"] = [MONTHS[i % len(MONTHS)] for i in range(n)]
    frame["vintage_year"] = 2003 + (np.arange(n) % 6)
    return frame


def training(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["period_ym"] <= "201612"]


@pytest.mark.parametrize("family", ["logistic", "lightgbm", "ebm"])
@pytest.mark.parametrize("start", [fx.CURRENT, fx.LATE_30])
def test_every_family_predicts_five_states_that_add_to_one(family: str, start: int) -> None:
    frame = book(start, 2500)
    settings = {"outer_bags": 1, "max_rounds": 200} if family == "ebm" else {}
    settings |= {"n_estimators": 40} if family == "lightgbm" else {}
    model = transition_pipeline.fit(family, start, training(frame), settings)
    p = model.predict_proba(transition_pipeline.columns(frame))
    assert p.shape == (len(frame), 5) and np.allclose(p.sum(axis=1), 1.0) and (p >= 0).all()
    contrib = model.contributions(transition_pipeline.columns(frame.head(20)))
    assert contrib.shape == (20, len(fx.TRANSITION_FEATURES))
    assert model.params()["start_state"] == fx.STATES[start]


def test_rare_destinations_get_a_constant_rate() -> None:
    frame = book(fx.CURRENT, 3000)
    model = transition_pipeline.fit("lightgbm", fx.CURRENT, frame, {"n_estimators": 20})
    # current -> 60 and current -> default are (almost) never seen: constants, recorded in the params
    assert fx.LATE_60 in model.constants and fx.LATE_30 in model.models
    assert set(model.params()["constants"]) >= {"60"}


def test_directions_follow_the_destination() -> None:
    feats = ("current_ltv", "credit_score", "rate_incentive")
    assert transitions.directions(fx.CURRENT, fx.LATE_30, feats) == [1, -1, 0]  # worse
    assert transitions.directions(fx.LATE_30, fx.CURRENT, feats) == [-1, 1, 0]  # a cure
    assert transitions.directions(fx.LATE_60, fx.PREPAID, feats) == [0, 0, 1]  # prepayment
    late = ("late_months_12", "credit_score")
    assert transitions.directions(fx.CURRENT, fx.LATE_30, late) == [1, -1]  # constrained when current
    assert transitions.directions(fx.LATE_30, fx.LATE_60, late) == [0, -1]  # free once already behind
    floored = transitions.floor(np.array([[0.0, 1.0, 0.0, 0.0, 0.0]]))
    assert floored.min() >= 0.9e-6 and floored.sum() == pytest.approx(1.0)
    assert (
        transitions.role(fx.LATE_60, fx.LATE_30) == "better"
        and transitions.role(fx.LATE_30, fx.LATE_30) == "stay"
    )
    assert transitions.start_code("30") == fx.LATE_30 and transitions.start_code(0) == fx.CURRENT
    with pytest.raises(ValueError):
        transitions.start_code("default")
    with pytest.raises(ValueError):
        transitions.make("forest", fx.CURRENT)


def test_walk_forward_tuning_uses_only_training_years() -> None:
    frame = training(book(fx.LATE_30, 4000))
    record = transition_pipeline.tune("logistic", fx.LATE_30, frame)
    assert record["folds"] == [list(f) for f in transition_pipeline.FOLDS]
    assert len(record["scores"]) == 2 and all(len(s) == 3 for s in record["scores"])
    assert record["chosen"] == record["grid"][int(np.argmin(record["average"]))]
    assert max(last for _, _, last in transition_pipeline.FOLDS) <= "201612"  # never 2017-2019
    assert len(transition_pipeline.GRID["lightgbm"]) == 9 and transition_pipeline.GRID["ebm"] == [{}]
    fixed = transition_pipeline.train("logistic", fx.LATE_30, frame, settings={"C": 0.5})[1]
    assert fixed["fixed"] and fixed["chosen"] == {"C": 0.5}


def test_evaluate_reports_every_move_with_the_bars() -> None:
    frame = book(fx.LATE_60, 5000)
    model, _ = transition_pipeline.train("logistic", fx.LATE_60, training(frame), do_tune=False)
    valid = frame[frame["period_ym"] >= "201701"]
    result = transition_pipeline.evaluate(model, valid, training(frame))
    assert result["adverse_move"] == "default" and result["reason_codes"]
    assert set(result["moves"]) == set(fx.STATES)
    assert "default_by_year" in result["calibration"] and "60_by_year" not in result["calibration"]
    whole = result["moves"]["default"]["whole_period"]
    assert whole["group"] == "2017-2019" and whole["applies"] == (whole["events"] >= 200)
    metrics_ = transition_pipeline.scalar_metrics(result)
    assert "val_default_auc" in metrics_ and "val_d30_log_loss" in metrics_
    assert all(v == v for v in metrics_.values())


def test_downsample_keeps_every_weighted_count() -> None:
    frame = book(fx.CURRENT, 6000)
    small = transition_pipeline.downsample(frame, 4, (fx.CURRENT, fx.PREPAID))
    assert len(small) < len(frame)
    for s in range(5):
        assert small.loc[small["state"] == s, "weight"].sum() == pytest.approx(
            frame.loc[frame["state"] == s, "weight"].sum()
        )
    assert (small["state"] == fx.LATE_30).sum() == (frame["state"] == fx.LATE_30).sum()  # every roll kept


def test_learning_curve_and_log_run(tmp_path, monkeypatch) -> None:
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    mlflow.set_tracking_uri(f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    mlflow.create_experiment("t", artifact_location=(tmp_path / "artifacts").as_uri())
    mlflow.set_experiment("t")
    frame = book(fx.LATE_30, 3000)
    train, valid = training(frame), frame[frame["period_ym"] >= "201701"]
    model, tuning = transition_pipeline.train("lightgbm", fx.LATE_30, train, settings={"n_estimators": 30},
                                              eval_frame=valid)  # fmt: skip
    assert set(model.rounds) >= {"60"} and model.rounds["60"]["best_round"] >= 1
    result = transition_pipeline.evaluate(model, valid, train)
    lc = transition_pipeline.learning_curve(
        lambda part: transition_pipeline.fit("lightgbm", fx.LATE_30, part, {"n_estimators": 30}),
        train, valid, (0.5, 1.0), full_model=model,
    )  # fmt: skip
    assert [r["fraction"] for r in lc] == [0.5, 1.0] and "valid_auc_adverse" in lc[0]
    fit_row = {
        "train": transition_pipeline.train_scores(model, train),
        "valid": transition_pipeline.scores(model, valid),
    }
    assert transition_pipeline.verdict(fit_row["train"], fit_row["valid"])
    run_id = transition_pipeline.log_run(mlflow, model, tuning, result, {"sensitivity": "none"}, {"rows": len(train)},
                                         train, {"fit": fit_row, "rounds": model.rounds, "learning": lc})  # fmt: skip
    run = mlflow.get_run(run_id)
    assert run.data.tags["transition"] == "30" and run.data.tags["family"] == "lightgbm"
    loaded = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")
    inner = loaded.unwrap_python_model().inner.model
    cols = transition_pipeline.columns(valid)
    assert np.allclose(inner.predict_proba(cols), model.predict_proba(cols))
    out = loaded.predict(valid[list(fx.TRANSITION_FEATURES)].head(3))
    assert list(out.columns) == ["p_current", "p_30", "p_60", "p_default", "p_prepaid"]


# ---------------------------------------------------------------------------- the projection


class Fixed:
    """A transition model with fixed probabilities (to check the projection by hand)."""

    def __init__(self, row: list[float]) -> None:
        self.row = np.asarray(row)
        self.features = fx.TRANSITION_FEATURES

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        return np.tile(self.row, (len(next(iter(X.values()))), 1))


MATRIX = {
    fx.CURRENT: [0.85, 0.10, 0.0, 0.0, 0.05],
    fx.LATE_30: [0.50, 0.20, 0.30, 0.0, 0.0],
    fx.LATE_60: [0.40, 0.0, 0.0, 0.60, 0.0],
}


def start_table(states: list[int]) -> pd.DataFrame:
    n = len(states)
    frame = pd.DataFrame({f: np.ones(n) for f in fx.TRANSITION_FEATURES})
    frame["loan_age"], frame["calendar_month"], frame["late_months_12"] = 10.0, 1.0, 0.0
    for col in multistate.HISTORY:
        frame[col] = 0.0
    frame["state_last_month"] = 0.0
    frame["state_prev"] = states
    frame["loan_id"] = [f"L{i}" for i in range(n)]
    frame["vintage_year"] = 2015
    frame["property_state"] = "TX"
    frame["upb_start"], frame["rate_start"], frame["remaining_start"] = 100000.0, 6.0, 300.0
    frame["orig_upb"], frame["orig_ltv"], frame["hpi_orig"] = 120000.0, 80.0, 100.0
    return frame


def macro() -> multistate.Macro:
    months = pd.date_range("2015-01-01", "2020-12-01", freq="MS")
    state = pd.DataFrame([{"state": "TX", "month": m, "variable": v, "value": val}
                          for m in months for v, val in (("hpi", 110.0), ("unemployment_rate", 4.0))])  # fmt: skip
    national = pd.DataFrame([{"month": m, "variable": "mortgage_rate_30y", "value": 4.0} for m in months])
    return multistate.Macro.from_tables(state, national)


def test_projection_matches_the_matrix_by_hand() -> None:
    models = {s: Fixed(r) for s, r in MATRIX.items()}
    start = start_table([fx.CURRENT, fx.LATE_60])
    proj = multistate.project(models, start, macro(), "201701", 3)
    T = np.array([MATRIX[s] for s in fx.LIVE_STATES])  # 3 x 5
    for i, s in enumerate([fx.CURRENT, fx.LATE_60]):
        v = np.zeros(3)
        v[s] = 1.0
        for k in range(3):
            step = v @ T
            assert proj.p_default[i, k] == pytest.approx(step[fx.DEFAULTED])
            assert proj.p_prepay[i, k] == pytest.approx(step[fx.PREPAID])
            v = step[:3]
    assert proj.months == ["201701", "201702", "201703"]


def test_inputs_roll_forward_with_the_economy() -> None:
    start = start_table([fx.CURRENT])
    x0 = multistate.month_inputs(start, macro(), "201701", 0, fx.TRANSITION_FEATURES)
    x3 = multistate.month_inputs(start, macro(), "201701", 3, fx.TRANSITION_FEATURES)
    assert x0["loan_age"][0] == 10 and x3["loan_age"][0] == 13 and x3["calendar_month"][0] == 4
    balance = multistate.scheduled_balance(np.array([100000.0]), np.array([6.0]), np.array([300.0]), 3)[0]
    assert x3["current_ltv"][0] == pytest.approx(100 * balance / (120000 / 0.8 * 110 / 100))
    assert x3["rate_incentive"][0] == pytest.approx(2.0) and x3["unemployment_change_12"][0] == pytest.approx(
        0.0
    )
    assert x3["forbearance_prev"][0] == 0.0
    # an annuity: 100,000 at 6% over 360 months has about 98,772 left after 12 payments; 0 at the end
    b = multistate.scheduled_balance(
        np.array([100000.0, 100000.0]), np.array([6.0, 0.0]), np.array([360.0, 360.0]), 12
    )
    assert b[0] == pytest.approx(98772.2, abs=1.0) and b[1] == pytest.approx(100000 * (1 - 12 / 360))
    assert multistate.scheduled_balance(np.array([5.0]), np.array([6.0]), np.array([10.0]), 10)[0] == 0.0


def test_late_input_is_a_whole_number() -> None:
    li = multistate.late_input
    assert li(np.array([4.0]), np.array([0.0]), 4, fx.CURRENT)[0] == 4.0
    assert li(np.array([0.0]), np.array([0.0]), 4, fx.CURRENT)[0] == 0.0  # a clean loan sees exactly 0
    assert li(np.array([0.0]), np.array([1.4]), 4, fx.LATE_30)[0] == 2.0  # 1 projected (rounded) + this month
    assert li(np.array([0.0]), np.array([0.0]), 1, fx.LATE_60)[0] == 2.0
    assert (
        li(np.array([11.0]), np.array([0.0]), 1, fx.LATE_60)[0] == 12.0
    )  # 11 still in the window + this month


def test_start_history_leaves_the_window_when_the_data_would() -> None:
    # late 1 and 2 months before the start, and 10 months before
    h = np.zeros((1, 12))
    h[0, [0, 1, 9]] = 1.0
    got = [float(multistate.start_late_in_window(h, k)[0]) for k in range(14)]
    # month k keeps the rows j <= 12 - k back: the row 10 back leaves at k = 3, rows 2 and 1 at 11 and 12
    assert got == [3.0, 3.0, 3.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 1.0, 0.0, 0.0]
    frame = pd.DataFrame(h, columns=list(multistate.HISTORY))
    frame["late_months_12"] = 3.0
    assert multistate.check_history(frame) == 0
    frame["late_months_12"] = 2.0
    assert multistate.check_history(frame) == 1


def test_recent_late_history_is_kept_not_faded() -> None:
    # a current loan late in the 2 months just before the start keeps 2 late months until month 10;
    # an even fade of its count (2) would have given 1 by month 6 and 0 by month 10
    models = {s: Recorder([1.0, 0.0, 0.0, 0.0, 0.0]) for s in fx.LIVE_STATES}
    start = start_table([fx.CURRENT])
    start["late_months_12"] = 2.0
    start["late_hist_1"], start["late_hist_2"] = 1.0, 1.0
    multistate.project(models, start, macro(), "201701", 13)
    seen = [float(x[0]) for x in models[fx.CURRENT].seen]  # months 0..12
    assert seen == [2.0] * 11 + [1.0, 0.0]


class Recorder(Fixed):
    """Fixed probabilities; records the late-month input it was given on each call."""

    def __init__(self, row: list[float]) -> None:
        super().__init__(row)
        self.seen: list[np.ndarray] = []

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        self.seen.append(np.asarray(X["late_months_12"]).copy())
        return super().predict_proba(X)


def test_clean_loans_never_see_a_fraction_of_a_late_month() -> None:
    models = {s: Recorder(r) for s, r in MATRIX.items()}
    proj = multistate.project(models, start_table([fx.CURRENT]), macro(), "201701", 30)
    seen = np.concatenate(models[fx.CURRENT].seen)
    assert np.all(seen == np.round(seen))  # whole months only
    assert 0.0 in seen and seen.max() >= 1.0  # the clean slice sees 0; slices that went late see 1+
    assert np.concatenate(models[fx.LATE_30].seen).min() >= 1.0  # the 30 model: at least this month
    assert np.concatenate(models[fx.LATE_60].seen).min() >= 2.0  # the 60 model: at least two
    assert proj.p_default.sum() > 0


def test_projected_late_months_leave_after_a_year() -> None:
    # 30 -> current for sure, then current for good: the late month counts for 11 months, then drops out
    models = {s: Recorder([1.0, 0.0, 0.0, 0.0, 0.0]) for s in fx.LIVE_STATES}
    start = start_table([fx.LATE_30])
    start["late_months_12"], start["late_hist_1"] = 1.0, 1.0  # late in December (the start row's own state)
    multistate.project(models, start, macro(), "201701", 16)
    seen = [float(x[0]) for x in models[fx.CURRENT].seen]  # projected months 1..15, one slice each
    assert seen[:11] == [1.0] * 11 and seen[11:] == [0.0] * 4


def test_projection_check_counts_only_observed_months() -> None:
    models = {s: Fixed(r) for s, r in MATRIX.items()}
    start = start_table([fx.LATE_60, fx.LATE_60, fx.CURRENT])
    proj = multistate.project(models, start, macro(), "201901", 24)
    actuals = pd.DataFrame({"loan_id": ["L0", "L1", "L2"], "last_ym": ["201901", "201903", "201906"],
                            "last_status": ["defaulted", "censored", "prepaid"]})  # fmt: skip
    result = multistate.check(proj, start, actuals, "201901")
    observed, event = multistate.observed_months("201901", actuals)
    assert list(observed) == [1, 2, 6] and list(event) == [fx.DEFAULTED, -1, fx.PREPAID]
    expected_def = proj.p_default[0, :1].sum() + proj.p_default[1, :2].sum() + proj.p_default[2, :6].sum()
    assert result.totals["default_projected"] == pytest.approx(expected_def)
    assert result.totals["default_actual"] == 1 and result.totals["prepaid_actual"] == 1
    year = [c for c in result.cells if c.by == "year" and c.event == "default"]
    assert [c.group for c in year] == ["2019", "2020"] and not any(c.applies for c in result.cells)
    assert result.passes and result.as_dict()["loans"] == 3
    bad = multistate.Check(cells=[multistate._cell("default", "year", "2019", 300, 600)])
    good = multistate.Check(cells=[multistate._cell("default", "year", "2019", 300, 310)])
    assert not bad.passes and multistate.newly_failing(bad, good) == bad.cells
    assert multistate.newly_failing(bad, bad) == []


def test_late_bands_and_the_band_bar() -> None:
    bands = transition_pipeline.late_band(np.array([0, 1, 2, 3, 4, 5, 6, 12, np.nan]))
    assert list(bands) == ["0", "1", "2-3", "2-3", "4-5", "4-5", "6+", "6+", "0"]


def test_current_logistic_uses_whole_month_cuts_for_late_months() -> None:
    frame = book(fx.CURRENT)
    rng = np.random.default_rng(5)
    late = np.where(rng.uniform(size=len(frame)) < 0.97, 0.0, rng.integers(1, 13, len(frame)).astype(float))
    frame["late_months_12"] = late
    # rolls to 30 rise with the late count, so the monotone merge keeps the whole-month cuts
    roll = rng.uniform(size=len(frame)) < 0.01 + 0.04 * late
    frame.loc[roll, "state"] = fx.LATE_30
    model = transition_pipeline.fit("logistic", fx.CURRENT, frame, {"C": 1.0})
    b = [x for x in model.bins if x.name == "late_months_12"][0]
    assert b.edges[0] == 0.0 and len(b.edges) >= 4  # not collapsed to a single cut
    # the 30-day model keeps its quantile cuts for late months (only "state last month" is fixed there)
    assert "late_months_12" not in transitions.FIXED_EDGES[fx.LATE_30]


def test_state_last_month_follows_the_projection() -> None:
    # 60 -> current for sure, then current -> current: the current model sees "60 last month" once, then 0
    models = {s: Recorder([1.0, 0.0, 0.0, 0.0, 0.0]) for s in fx.LIVE_STATES}

    class Seen(Recorder):
        def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
            self.seen.append(np.asarray(X["state_last_month"]).copy())
            return Fixed.predict_proba(self, X)

    models = {s: Seen([1.0, 0.0, 0.0, 0.0, 0.0]) for s in fx.LIVE_STATES}
    start = start_table([fx.LATE_60])
    start["state_last_month"] = 1.0  # it was 30 days late the month before the start
    start["late_months_12"], start["late_hist_1"], start["late_hist_2"] = 2.0, 1.0, 1.0
    multistate.project(models, start, macro(), "201701", 4)
    assert float(models[fx.LATE_60].seen[0][0]) == 1.0  # month 0: the start row's own value
    assert [float(x[0]) for x in models[fx.CURRENT].seen] == [2.0, 0.0, 0.0]


def test_late_states_record_where_they_came_from() -> None:
    assert list(multistate.start_slices(np.array([0, 0, 0, 1, 2]), np.array([0, 1, 2, 2, 1]))) == [
        multistate.CLEAN,
        1,
        multistate.FROM_60,
        2,
        1,
    ]
    assert list(
        multistate.last_month_state(fx.CURRENT, np.array([1, 5, multistate.FROM_60, multistate.CLEAN]))
    ) == [1.0, 0.0, 2.0, 0.0]
    assert list(multistate.last_month_state(fx.LATE_30, np.array([0, 1, 2]))) == [0.0, 1.0, 2.0]


def test_the_december_month_is_not_counted_twice() -> None:
    # a loan 60 days late at the start (late in December) that stays at 60: in month 1 its late months
    # are the history (December and November) plus this month's own state (January), not 4
    models = {s: Recorder([0.0, 0.0, 1.0, 0.0, 0.0]) for s in fx.LIVE_STATES}
    start = start_table([fx.LATE_60])
    start["state_last_month"] = 1.0
    start["late_months_12"], start["late_hist_1"], start["late_hist_2"] = 2.0, 1.0, 1.0
    multistate.project(models, start, macro(), "201701", 2)
    assert float(models[fx.LATE_60].seen[1][0]) == 3.0


def test_check_reports_by_starting_state_and_month() -> None:
    models = {s: Fixed(r) for s, r in MATRIX.items()}
    start = start_table([fx.LATE_60, fx.CURRENT])
    proj = multistate.project(models, start, macro(), "201901", 3)
    actuals = pd.DataFrame({"loan_id": ["L0", "L1"], "last_ym": ["201901", "201903"],
                            "last_status": ["defaulted", "current"]})  # fmt: skip
    c = multistate.check(proj, start, actuals, "201901")
    assert (
        c.by_start_state["60"]["default_actual"] == 1.0
        and c.by_start_state["current"]["default_actual"] == 0.0
    )
    assert c.by_start_state["60"]["default_projected"] == pytest.approx(proj.p_default[0, :1].sum())
    assert [m["month"] for m in c.monthly] == ["201901", "201902", "201903"]
    assert c.monthly[0]["default_actual"] == 1.0
    assert sum(m["default_projected"] for m in c.monthly) == pytest.approx(c.totals["default_projected"])
