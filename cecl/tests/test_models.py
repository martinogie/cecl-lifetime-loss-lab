"""The model code on a small made-up book (needs requirements-ml.txt; skipped without it)."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
pytest.importorskip("lightgbm")
pytest.importorskip("interpret")

from cecl import features as fx  # noqa: E402
from cecl.models import binning, families, pipeline, reasons, selection  # noqa: E402


def book(n: int = 6000, seed: int = 1) -> pd.DataFrame:
    """Loan-months where default rises with LTV and lateness, and prepayment with the rate incentive."""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({f: rng.uniform(0, 1, n) for f in fx.FEATURE_NAMES})
    frame["current_ltv"] = rng.uniform(40, 130, n)
    frame["credit_score"] = rng.uniform(600, 820, n)
    frame["rate_incentive"] = rng.normal(0, 1.5, n)
    frame["months_late_prev"] = rng.choice([0, 0, 0, 1, 2], n).astype(float)
    frame.loc[rng.uniform(size=n) < 0.02, "current_ltv"] = np.nan  # some missing values
    z = (
        0.06 * (frame["current_ltv"].fillna(80) - 80)
        + 1.2 * frame["months_late_prev"]
        - 0.01 * (frame["credit_score"] - 720)
    )
    p_def = 1 / (1 + np.exp(-(z - 4)))
    p_pre = 1 / (1 + np.exp(-(1.0 * frame["rate_incentive"] - 3)))
    u = rng.uniform(size=n)
    frame["outcome"] = np.where(u < p_def, fx.DEFAULT, np.where(u < p_def + p_pre, fx.PREPAY, fx.NONE))
    frame["weight"] = np.where(frame["outcome"] == fx.NONE, 2.0, 1.0)
    frame["loan_id"] = [f"L{i % 800:04d}" for i in range(n)]
    months = [f"{y}{m:02d}" for y in range(2010, 2019) for m in range(1, 13)]
    frame["period_ym"] = [months[i % len(months)] for i in range(n)]
    frame["vintage_year"] = 2009 + (np.arange(n) % 5)
    return frame


def test_bins_are_monotone_where_expected() -> None:
    frame = book()
    b = binning.fit_bins(
        "current_ltv", frame["current_ltv"], frame["outcome"] == fx.DEFAULT, frame["weight"], 1
    )
    rates = b.rates[:-1]  # the last bin is "missing"
    assert all(later >= earlier for earlier, later in zip(rates, rates[1:], strict=False))
    idx = b.assign(np.array([np.nan, -1.0, 1e9]))
    assert idx[0] == b.n_bins and idx[1] == 0 and idx[2] == b.n_bins - 1
    flag = binning.fit_bins("term_over_360", [0, 1, 0, 1], [0, 1, 0, 0], [1, 1, 1, 1])
    assert flag.edges == [0.0]
    assert binning._merge_empty([1.0, 2.0], [1.0, 0.0, 2.0], [5.0, 0.0, 4.0]) == (
        [1.0],
        [1.0, 2.0],
        [5.0, 4.0],
    )


@pytest.mark.parametrize("name", ["logistic", "lightgbm", "ebm"])
def test_each_family_fits_predicts_and_explains(name: str) -> None:
    frame = book()
    settings = {"ebm": {"interactions": 0, "outer_bags": 1, "max_rounds": 200},
                "lightgbm": {"n_estimators": 60}, "logistic": {}}[name]  # fmt: skip
    model = families.make(name, **settings).fit(pipeline.columns(frame), frame["outcome"].to_numpy(),
                                                 frame["weight"].to_numpy())  # fmt: skip
    p = model.predict_proba(pipeline.columns(frame))
    assert p.shape == (len(frame), 3) and np.allclose(p.sum(axis=1), 1.0) and (p >= 0).all()
    c = model.contributions(pipeline.columns(frame.head(50)))
    assert c.shape == (50, len(fx.FEATURE_NAMES))
    ltv = fx.FEATURE_NAMES.index("current_ltv")
    high, low = frame.head(50)["current_ltv"].fillna(80) > 110, frame.head(50)["current_ltv"].fillna(80) < 60
    assert c[high.to_numpy(), ltv].mean() > c[low.to_numpy(), ltv].mean()  # high LTV raises the default score
    assert model.params()["family"] == name


def test_unknown_family_and_combine() -> None:
    with pytest.raises(ValueError, match="unknown family"):
        families.make("forest")
    p = families._combine(np.array([0.7]), np.array([0.6]))
    assert np.allclose(p, [[0.0, 0.7 / 1.3, 0.6 / 1.3]])


def test_selection_rule_follows_adr_009() -> None:
    def r(name: str, ll: float, cal: bool = True, codes: bool = True) -> selection.FamilyResult:
        return selection.FamilyResult(name, ll, cal, codes)

    assert (
        selection.choose([r("logistic", 0.100), r("lightgbm", 0.099), r("ebm", 0.0995)]).chosen == "logistic"
    )
    assert (
        selection.choose([r("logistic", 0.100), r("lightgbm", 0.090), r("ebm", 0.097)]).chosen == "lightgbm"
    )
    # EBM is within 2% of LightGBM's log loss and simpler: it wins the tie
    choice = selection.choose([r("logistic", 0.100), r("lightgbm", 0.090), r("ebm", 0.0915)])
    assert choice.chosen == "ebm" and "simplest family within 2%" in choice.reasons[-1]
    blocked = selection.choose(
        [r("logistic", 0.100, cal=False), r("lightgbm", 0.08, cal=False), r("ebm", 0.08, codes=False)]
    )
    assert blocked.chosen == "logistic"
    text = " ".join(blocked.reasons)
    assert (
        "fails a calibration bar" in text and "no reason codes" in text and "benchmark itself fails" in text
    )
    with pytest.raises(ValueError, match="benchmark"):
        selection.choose([r("lightgbm", 0.1)])


def test_reason_codes() -> None:
    values = {f: np.array([1.0, 0.0]) for f in fx.FEATURE_NAMES}
    values["current_ltv"] = np.array([112.0, 60.0])
    values["credit_score"] = np.array([np.nan, 780.0])
    contrib = np.zeros((2, len(fx.FEATURE_NAMES)))
    contrib[0, fx.FEATURE_NAMES.index("current_ltv")] = 0.9
    contrib[0, fx.FEATURE_NAMES.index("credit_score")] = 0.4
    contrib[0, fx.FEATURE_NAMES.index("mi_pct")] = -0.5  # lowers risk: never a reason
    codes = reasons.top_reasons(contrib, values)
    assert codes[0] == [
        "Current loan-to-value 112% (owes more than the house is worth)",
        "Credit score not available",
    ]
    assert codes[1] == []
    table = reasons.wording_table()
    assert len(table) == len(fx.model_features("buckets") + fx.TRANSITION_EXTRA) and all(
        row["example"] for row in table
    )


def test_pipeline_train_evaluate_and_downsample() -> None:
    frame = book(8000)
    train = frame[frame["period_ym"] <= "201612"]
    valid = frame[frame["period_ym"] >= "201701"]
    model, tuning = pipeline.train("logistic", train, tune=True)
    assert tuning["chosen"] in pipeline.TUNING_GRID["logistic"] and len(tuning["scores"]) == 2
    horizon = valid.assign(weight=1.0)
    result = pipeline.evaluate(model, valid, horizon, train, "201701")
    for key in (
        "log_loss",
        "log_loss_default",
        "auc_default",
        "calibration",
        "psi_by_year",
        "auc_by_horizon",
    ):
        assert key in result
    assert result["reason_codes"] is True and set(result["auc_by_horizon"]) == {"12", "24"}
    scalars = pipeline.scalar_metrics(result, "val")
    assert "val_log_loss_default" in scalars and "val_auc_12m" in scalars
    assert scalars["val_gini_default"] == pytest.approx(2 * scalars["val_auc_default"] - 1)
    assert scalars["val_gini_12m"] == pytest.approx(2 * scalars["val_auc_12m"] - 1)
    small = pipeline.downsample_no_events(train, 4)
    events = (train["outcome"] != fx.NONE).sum()
    assert (small["outcome"] != fx.NONE).sum() == events and len(small) < len(train)
    # weighted no-event months are unchanged, stratum by stratum
    assert small.loc[small["outcome"] == fx.NONE, "weight"].sum() == pytest.approx(
        train.loc[train["outcome"] == fx.NONE, "weight"].sum()
    )
    assert pipeline.downsample_no_events(train, 1) is train


def test_pyfunc_wrapper_predicts_a_frame() -> None:
    frame = book(2000)
    model, _ = pipeline.train("logistic", frame, tune=False)
    out = pipeline.PDModel(model).predict(None, frame[list(fx.FEATURE_NAMES)])
    assert list(out.columns) == ["p_none", "p_default", "p_prepay"] and len(out) == len(frame)


def test_log_run_and_reload_like_the_test_notebook(tmp_path, monkeypatch) -> None:
    """log_run writes a model the out-of-time notebook can load back and unwrap (local MLflow store)."""
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    mlflow.set_tracking_uri(f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    # artefacts in the test's own folder too (by default MLflow writes ./mlruns in the project)
    mlflow.create_experiment("test", artifact_location=(tmp_path / "artifacts").as_uri())
    mlflow.set_experiment("test")
    frame = book(3000)
    model, tuning = pipeline.train("logistic", frame, tune=False)
    result = pipeline.evaluate(model, frame, None, frame, "201701")
    from cecl.models import curves

    curve_data = {
        "fit": {
            "train": curves.train_scores(model, frame, pipeline.inputs),
            "valid": curves.scores(model, frame, pipeline.inputs),
        },
        "learning": curves.learning_curve(
            lambda part: pipeline.train("logistic", part, tune=False)[0],
            frame,
            frame,
            pipeline.inputs,
            (0.5, 1.0),
            full_model=model,
        ),
    }
    run_id = pipeline.log_run(
        mlflow, "logistic", model, tuning, result, {"sensitivity": "none"}, {"rows": 3000}, frame, curve_data
    )
    client = mlflow.MlflowClient()
    arts = {a.path for a in client.list_artifacts(run_id)} | {
        a.path for a in client.list_artifacts(run_id, "curves")
    }
    assert {"curves.json", "curves/learning_curve.png"} <= arts
    assert mlflow.get_run(run_id).data.tags["fit_verdict"]
    loaded = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")
    inner = loaded.unwrap_python_model().inner.model
    assert np.allclose(
        inner.predict_proba(pipeline.columns(frame)), model.predict_proba(pipeline.columns(frame))
    )
    out = loaded.predict(frame[list(fx.FEATURE_NAMES)].head(5))
    assert list(out.columns) == ["p_none", "p_default", "p_prepay"]
    stored = mlflow.artifacts.load_dict(f"runs:/{run_id}/validation.json")
    assert stored["log_loss_default"] == pytest.approx(result["log_loss_default"])
    assert mlflow.get_run(run_id).data.tags["family"] == "logistic"


def test_evaluate_records_a_family_that_cannot_explain_itself() -> None:
    frame = book(2000)
    model, _ = pipeline.train("logistic", frame, tune=False)

    class Mute:
        def predict_proba(self, X):
            return model.predict_proba(X)

        def contributions(self, X):
            raise NotImplementedError("no explanations")

    result = pipeline.evaluate(Mute(), frame, None, frame, "201701")
    assert result["reason_codes"] is False and "NotImplementedError" in result["reason_error"]


def test_train_with_fixed_settings_skips_tuning() -> None:
    frame = book(2000)
    model, record = pipeline.train("logistic", frame, settings={"C": 1.0})
    assert record == {"grid": [{"C": 1.0}], "scores": [], "chosen": {"C": 1.0}, "fixed": True}
    assert model.C == 1.0


def test_diagnostics_split_status_years_and_hurricane_states() -> None:
    from cecl.models import diagnostics

    frame = book(4000)
    frame["property_state"] = np.where(np.arange(len(frame)) % 3 == 0, "FL", "OH")
    train = frame[frame["period_ym"] <= "201612"]
    valid = frame[frame["period_ym"] >= "201701"]
    model, _ = pipeline.train("logistic", train, tune=False)  # fitted on the training years only
    out = diagnostics.run_all(model, train, valid, pipeline.inputs)
    status = out["start_status_validation"]
    assert set(status) == {"current", "delinquent"}
    assert status["current"]["share_of_defaults"] + status["delinquent"][
        "share_of_defaults"
    ] == pytest.approx(1.0)
    assert set(out["calendar_year_training"]) == {str(y) for y in range(2010, 2017)}
    # logistic regression with an intercept: in-sample predicted defaults add up to the actual ones
    yrs = out["calendar_year_training"].values()
    assert sum(r["predicted"] for r in yrs) == pytest.approx(sum(r["events"] for r in yrs), rel=0.02)
    assert set(out["hurricanes_2017_2018"]) == {"hurricane_states", "other_states"}
    assert set(out["prepay_by_year_training"]) == {str(y) for y in range(2010, 2017)}
    assert all("ratio" in r for r in out["prepay_by_year_validation"].values())


@pytest.mark.parametrize("era", ["binary", "buckets"])
def test_era_feature_is_derived_and_used(era: str) -> None:
    from cecl.models import families as fam

    frame = book(3000)
    feats = tuple(f.name for f in fx.model_features(era))
    cols = pipeline.columns(frame, feats)
    assert "originated_2009_on" in cols and np.array_equal(
        cols["originated_2009_on"], (frame["vintage_year"] >= 2009).astype(float)
    )
    model, _ = pipeline.train(
        "lightgbm", frame, settings={"n_estimators": 30}, features=feats, eval_frame=frame.head(500)
    )
    assert fam.feature_list(model) == feats and model.contributions(
        pipeline.inputs(model, frame.head(5))
    ).shape == (5, len(feats))
    assert (
        len(model.rounds["default"]["validation"]) == 30 and 1 <= model.rounds["default"]["best_round"] <= 30
    )
    result = pipeline.evaluate(model, frame, None, frame, "201701")
    assert result["reason_codes"] is True
    with pytest.raises(ValueError, match="era must be"):
        fx.model_features("decades")


def test_models_saved_without_a_feature_list_still_work() -> None:
    from cecl.models import families as fam

    frame = book(2000)
    model, _ = pipeline.train("logistic", frame, tune=False)
    del model.features  # as pickled before 17 Nov 2026
    assert fam.feature_list(model) == fx.FEATURE_NAMES
    assert model.predict_proba(pipeline.inputs(model, frame)).shape == (len(frame), 3)


def test_learning_curve_table_verdict_and_figures(tmp_path, monkeypatch) -> None:
    from cecl.models import curves

    frame = book(4000)
    train, valid = frame[frame["period_ym"] <= "201612"], frame[frame["period_ym"] >= "201701"]
    sub = curves.subsample(train, 0.25)
    assert len(sub) < len(train) and sub["weight"].sum() == pytest.approx(train["weight"].sum(), rel=0.1)
    assert curves.subsample(train, 1.0) is train
    full, _ = pipeline.train("logistic", train, tune=False)
    rows = curves.learning_curve(lambda part: pipeline.train("logistic", part, tune=False)[0], train, valid,
                                 pipeline.inputs, (0.25, 1.0), full_model=full)  # fmt: skip
    assert [r["fraction"] for r in rows] == [0.25, 1.0] and "valid_gini_default" in rows[0]
    table = curves.fit_table({"logistic": {"train": curves.train_scores(full, train, pipeline.inputs),
                                           "valid": curves.scores(full, valid, pipeline.inputs)}})  # fmt: skip
    assert table[0]["family"] == "logistic" and table[0]["verdict"]
    t, v = {"log_loss_default": 0.010, "auc_default": 0.95}, {"log_loss_default": 0.020, "auc_default": 0.85}
    assert curves.verdict(t, v).startswith("overfitting")
    assert "level shift" in curves.verdict(t, {"log_loss_default": 0.02, "auc_default": 0.945})
    assert curves.verdict(t, {"log_loss_default": 0.0105, "auc_default": 0.948}).startswith("generalises")
    assert curves.verdict(t, {"log_loss_default": 0.0118, "auc_default": 0.93}).startswith("mild")
    fig = curves.learning_figure(rows, "logistic")
    fig.savefig(tmp_path / "lc.png")
    gbm, _ = pipeline.train("lightgbm", train, settings={"n_estimators": 20}, eval_frame=valid)
    fig2 = curves.round_figure(gbm.rounds, "lightgbm")
    fig2.savefig(tmp_path / "rounds.png")
    assert (tmp_path / "lc.png").stat().st_size > 0 and (tmp_path / "rounds.png").stat().st_size > 0
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    mlflow.set_tracking_uri(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    mlflow.create_experiment("curves", artifact_location=(tmp_path / "a").as_uri())
    mlflow.set_experiment("curves")
    with mlflow.start_run() as run:
        curves.log_rounds(mlflow, gbm.rounds)
    history = mlflow.MlflowClient().get_metric_history(run.info.run_id, "round_default_validation_log_loss")
    assert len(history) == 20 and history[-1].step == 20


def test_diagnostic_training_sample_keeps_every_default() -> None:
    from cecl.models import diagnostics

    frame = book(6000)
    sample = diagnostics.training_sample(frame, 5)
    defaults = frame["outcome"] == fx.DEFAULT
    assert (sample["outcome"] == fx.DEFAULT).sum() == defaults.sum()  # actual defaults exact
    assert len(sample) < len(frame) / 2
    others = frame.loc[~defaults, "weight"].sum()
    assert sample.loc[sample["outcome"] != fx.DEFAULT, "weight"].sum() == pytest.approx(others, rel=0.1)
    assert diagnostics.training_sample(frame, 1) is frame


def test_ebm_early_stopping_is_time_based() -> None:
    from cecl.models import families as fam

    periods = np.array(["201005", "201312", "201401", "201612", "200101"] * 400)
    bags = fam.time_bags(periods, 3, seed=1)
    assert bags.shape == (2000, 3)
    late = periods >= "201401"
    assert (bags[late] == -1).all()  # 2014-2016: the stopping set in every bag
    assert set(np.unique(bags[~late])) <= {0, 1} and (bags[~late] == 1).mean() == pytest.approx(
        0.85, abs=0.03
    )
    assert not np.array_equal(bags[:, 0], bags[:, 1])  # the bags differ
    frame = book(3000)
    frame["period_ym"] = np.where(np.arange(len(frame)) % 4 == 0, "201505", "201105")
    timed, _ = pipeline.train(
        "ebm", frame, settings={"interactions": 0, "outer_bags": 2, "max_rounds": 100, "time_stopping": True}
    )
    assert timed.params()["early_stopping"].startswith("time")
    default, _ = pipeline.train(
        "ebm", frame, settings={"interactions": 0, "outer_bags": 2, "max_rounds": 100}
    )
    assert default.params()["early_stopping"] == "random 15% per bag"  # the default (L-025)


def test_horizon_auc_uses_only_start_date_inputs() -> None:
    """The start-date horizon AUC never sees a loan's later months: changing them doesn't change it."""
    frame = book(6000)
    model, _ = pipeline.train("logistic", frame, tune=False)
    horizon = frame.assign(weight=1.0)
    base = pipeline.horizon_from_start(model, horizon, "201701")
    later = horizon.copy()
    after = later["period_ym"] > "201701"
    later.loc[after, "months_late_prev"] = 9.0  # scramble every input after the start month
    later.loc[after, "current_ltv"] = 300.0
    again = pipeline.horizon_from_start(model, later, "201701")
    assert set(base) == {"12", "24"} and base["12"]["method"] == "start-date inputs only"
    assert (
        base["12"]["auc"] == pytest.approx(again["12"]["auc"]) and base["24"]["loans"] == again["24"]["loans"]
    )
    assert pipeline.horizon_from_start(model, horizon, "203001") == {}


# ---------------------------------------------------------------------------- the out-of-time test
def test_oot_verdict_is_on_judged_default_cells_only() -> None:
    from cecl.models import multistate, oot

    def cell(event: str, ratio: float, applies: bool = True) -> multistate.Cell:
        return multistate.Cell(event, "vintage", "2016", 300.0, 300.0 * ratio, ratio, applies,
                               (not applies) or 0.80 <= ratio <= 1.25)  # fmt: skip

    ok = multistate.Check(
        cells=[cell("default", 1.1), cell("prepaid", 0.6), cell("default", 2.0, applies=False)]
    )
    v = oot.verdict(ok)
    assert v["passes"] and v["judged_default_cells"] == 1 and v["judged_payoff_cells"] == 1
    assert len(v["failing_payoff_cells_recorded_under_L028"]) == 1  # a payoff failure does not fail the test
    bad = oot.verdict(multistate.Check(cells=[cell("default", 1.27), cell("prepaid", 1.0)]))
    assert not bad["passes"] and bad["failing_default_cells"][0]["ratio"] == 1.27
    assert (oot.TEST_START, oot.TEST_END, oot.TEST_MONTHS) == ("202207", "202606", 48)


def test_oot_guards() -> None:
    from cecl.models import oot

    assert oot.has_marker("x\n" + oot.EXPECTATIONS_FINAL + " (18 Nov)\n", oot.EXPECTATIONS_FINAL)
    assert not oot.has_marker("- **Status:** draft, not final", oot.EXPECTATIONS_FINAL)
    locked = oot.locked_set({"run_current": "a", "run_30": "b", "run_60": "c", "info_30_ebm": "d"})
    assert locked == {"current": "a", "30": "b", "60": "c"}
    with pytest.raises(ValueError):
        oot.locked_set({"run_current": "a", "run_30": "b"})
    tags = [{"transition": "current", "family": "logistic", "sensitivity": "covid_in_training"},
            {"transition": "30", "family": "lightgbm", "sensitivity": "covid_in_training"},
            {"transition": "60", "family": "logistic", "sensitivity": "none"}]  # fmt: skip
    assert oot.covid_sensitivity_missing(tags, "logistic") == ["30", "60"]
    assert oot.challenger_mismatches(locked, {"current": "a", "30": "x", "60": None}) == ["30", "60"]
    assert oot.registered_name("30") == "pd_transition_30"
    with pytest.raises(ValueError):
        oot.registered_name("90")
