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
    assert len(table) == len(fx.FEATURE_NAMES) and all(row["example"] for row in table)


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
    run_id = pipeline.log_run(
        mlflow, "logistic", model, tuning, result, {"sensitivity": "none"}, {"rows": 3000}, frame
    )
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
