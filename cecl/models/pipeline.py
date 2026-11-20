"""Train, evaluate and log one model family (Phase 2 steps 3-7), engine-agnostic.

The Databricks notebooks read the feature tables into pandas and call these functions; the PC's
tests call them on small made-up frames. Nothing here reads files or Spark.

- `train`: fits a family on the training months (plus the COVID months for the sensitivity run),
  optionally choosing among a few settings on the last training years first (time-ordered: fit on
  1999-2013, judge on 2014-2016, then refit on all training years).
- `evaluate`: every measure, weighted (cecl/metrics.py): log loss overall and per cause, AUC per
  cause, calibration by year and by vintage for both causes against the ADR-009 bar, PSI of the PD
  by year against the training months, AUC at 12/24/36 months on the horizon table, and a reason-code
  check.
- `log_run`: everything to MLflow (parameters, measures, tables as JSON artefacts, the model).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import numpy as np

from cecl import features as fx
from cecl import metrics
from cecl.models import families, reasons

TUNING_GRID: dict[str, list[dict[str, Any]]] = {
    "logistic": [{"C": 0.1}, {"C": 1.0}],
    "lightgbm": [{"num_leaves": 15}, {"num_leaves": 31}, {"num_leaves": 63}],
    "ebm": [{}],  # the slowest family: default settings only (Phase 2 plan, allowance rules)
}
INNER_TRAIN_LAST, INNER_VALID_FIRST = "201312", "201401"
HORIZONS = (12, 24, 36)
REASON_CHECK_ROWS = 2000
PSI_BASELINE_ROWS = 200_000  # training rows scored for the PSI baseline (fixed sample)


def columns(frame: Any, features: tuple[str, ...] = fx.FEATURE_NAMES) -> dict[str, np.ndarray]:
    """The model inputs from a feature-table frame; derived features (the origination era, ADR-010)
    are computed from `vintage_year`."""
    out = {}
    for f in features:
        if f in frame:
            out[f] = frame[f].to_numpy(dtype=float)
        else:
            out[f] = fx.DERIVED[f](frame["vintage_year"].to_numpy())
    return out


def inputs(model: Any, frame: Any) -> dict[str, np.ndarray]:
    """The inputs for a trained model (its own feature list)."""
    return columns(frame, families.feature_list(model))


def downsample_no_events(frame: Any, every: int) -> Any:
    """A smaller training sample for a slow family (the EBM): every event kept; 1 in `every` of the
    kept no-event months, by a fixed hash, re-weighted per stratum (vintage x year) so the weighted
    no-event counts are unchanged. Documented in the run when used."""
    import pandas as pd

    if every <= 1:
        return frame
    h = pd.util.hash_pandas_object(frame[["loan_id", "period_ym"]], index=False).to_numpy() % every
    none = frame["outcome"].to_numpy() == fx.NONE
    keep = (~none) | (h == 0)
    out = frame[keep].copy()
    stratum = frame["vintage_year"].astype(str) + "|" + frame["period_ym"].str[:4]
    full = (frame["weight"] * none).groupby(stratum).sum()
    kept_none = out["outcome"] == fx.NONE
    kept = (out["weight"] * kept_none).groupby(stratum[keep]).sum()
    factor = (full / kept).reindex(stratum[keep]).to_numpy()
    out.loc[kept_none.to_numpy(), "weight"] = (out["weight"] * factor)[kept_none.to_numpy()]
    return out


def _fit(name: str, frame: Any, settings: dict[str, Any], features: tuple[str, ...] = fx.FEATURE_NAMES,
         eval_frame: Any = None) -> Any:  # fmt: skip
    model = families.make(name, features=features, **settings)
    eval_set = None
    if eval_frame is not None:
        eval_set = (columns(eval_frame, features), eval_frame["outcome"].to_numpy(dtype=int),
                    eval_frame["weight"].to_numpy(dtype=float))  # fmt: skip
    extra = {"periods": frame["period_ym"].to_numpy()} if name == "ebm" else {}  # time-based EBM stopping
    return model.fit(columns(frame, features), frame["outcome"].to_numpy(dtype=int),
                     frame["weight"].to_numpy(dtype=float), eval_set=eval_set, **extra)  # fmt: skip


def train(
    name: str,
    frame: Any,
    tune: bool = True,
    settings: dict[str, Any] | None = None,
    features: tuple[str, ...] = fx.FEATURE_NAMES,
    eval_frame: Any = None,
) -> tuple[Any, dict[str, Any]]:
    """Fit a family on `frame` (training months); returns (model, tuning record).

    `settings` fixes the family's settings and skips tuning (for example to re-run with the setting
    an earlier run's tuning chose, without spending the allowance on tuning again). `eval_frame`
    (validation rows) is only recorded as a training curve (LightGBM), never used to fit or tune."""
    if settings is not None:
        return _fit(name, frame, settings, features, eval_frame), {
            "grid": [settings],
            "scores": [],
            "chosen": settings,
            "fixed": True,
        }
    grid = TUNING_GRID[name] if tune else [{}]
    record: dict[str, Any] = {"grid": grid, "scores": []}
    best = grid[0]
    if len(grid) > 1:
        inner_train = frame[frame["period_ym"] <= INNER_TRAIN_LAST]
        inner_valid = frame[frame["period_ym"] >= INNER_VALID_FIRST]
        scores = []
        for settings in grid:
            m = _fit(name, inner_train, settings, features)
            p = m.predict_proba(columns(inner_valid, features))
            scores.append(metrics.cause_log_loss(inner_valid["outcome"], p, inner_valid["weight"], "default"))
        record["scores"] = scores
        best = grid[int(np.argmin(scores))]
    record["chosen"] = best
    return _fit(name, frame, best, features, eval_frame), record


def horizon_from_start(
    model: Any, horizon: Any, start: str, months: tuple[int, ...] = HORIZONS
) -> dict[str, Any]:
    """AUC (and Gini) at 12, 24 and 36 months using **only what is known at the start date**.

    Each loan alive in `start` is scored from its start-month row alone and projected forward: loan
    age and calendar month advance, every other input (including delinquency, unemployment, LTV)
    stays at its start value, since later values are not known when the forecast is made. Monthly
    default and prepayment chances are combined as competing risks into the chance of defaulting
    within each horizon (`metrics.cumulative_default`); loans are ranked by it against whether they
    actually defaulted. (Replaces the 17 Nov 2026 version, which used each loan's actual later months,
    including later delinquency, and so overstated ranking power.)
    """
    import pandas as pd

    last = horizon["period_ym"].max()
    usable = [m for m in months if metrics._add_months(start, m - 1) <= last]
    if not usable:
        return {}
    h = max(usable)
    at_start = horizon[horizon["period_ym"] == start].drop_duplicates("loan_id")
    if at_start.empty:
        return {}
    rep = at_start.loc[at_start.index.repeat(h)].copy()
    step = np.tile(np.arange(h), len(at_start))
    rep["loan_age"] = rep["loan_age"].to_numpy() + step
    month0 = rep["calendar_month"].to_numpy() - 1
    rep["calendar_month"] = ((month0 + step) % 12 + 1).astype(float)
    p = model.predict_proba(inputs(model, pd.DataFrame(rep)))
    n = len(at_start)
    cif = metrics.cumulative_default(p[:, fx.DEFAULT].reshape(n, h), p[:, fx.PREPAY].reshape(n, h))
    ids = at_start["loan_id"].to_numpy()
    result = {}
    for m in usable:
        labels = metrics.horizon_labels(horizon["loan_id"].to_numpy(), horizon["period_ym"].to_numpy(),
                                        horizon["outcome"].to_numpy(), start, m)  # fmt: skip
        keep = np.array([i in labels for i in ids])
        y = np.array([labels[i] for i in ids[keep]])
        value = metrics.auc(y == 1, cif[keep, m - 1])
        result[str(m)] = {"auc": value, "gini": metrics.gini(value), "loans": int(keep.sum()),
                          "defaults": int(y.sum()), "method": "start-date inputs only"}  # fmt: skip
    return result


def evaluate(model: Any, frame: Any, horizon: Any | None, train_frame: Any, start: str) -> dict[str, Any]:
    """All measures on one period (validation, or the test months), weighted."""
    y = frame["outcome"].to_numpy(dtype=int)
    w = frame["weight"].to_numpy(dtype=float)
    p = model.predict_proba(inputs(model, frame))
    out: dict[str, Any] = {
        "rows": int(len(frame)),
        "weighted_months": float(w.sum()),
        "log_loss": metrics.log_loss(y, p, w),
        "log_loss_default": metrics.cause_log_loss(y, p, w, "default"),
        "log_loss_prepay": metrics.cause_log_loss(y, p, w, "prepay"),
        "auc_default": metrics.auc(y == fx.DEFAULT, p[:, fx.DEFAULT], w),
        "auc_prepay": metrics.auc(y == fx.PREPAY, p[:, fx.PREPAY], w),
    }
    out["gini_default"] = metrics.gini(out["auc_default"])  # Gini = 2 x AUC - 1, next to every AUC
    out["gini_prepay"] = metrics.gini(out["auc_prepay"])
    calibration: dict[str, Any] = {}
    passes = True
    for by, groups in (("year", frame["period_ym"].str[:4]), ("vintage", frame["vintage_year"].astype(str))):
        for cause in ("default", "prepay"):
            rows = metrics.calibration(groups.to_numpy(), y, p, w, cause)
            calibration[f"{cause}_by_{by}"] = [asdict(r) for r in rows]
            passes = passes and all(r.passes for r in rows)
    out["calibration"] = calibration
    out["calibration_passes"] = passes
    # PSI needs only the training score distribution: a fixed sample of the training rows gives it
    # (scoring all 2.2 million training rows was most of the evaluation time, 17 Nov 2026).
    baseline = train_frame.sample(n=min(PSI_BASELINE_ROWS, len(train_frame)), random_state=7)
    p_train = model.predict_proba(inputs(model, baseline))[:, fx.DEFAULT]
    out["psi_by_year"] = {
        year: metrics.psi(p_train, p[(frame["period_ym"].str[:4] == year).to_numpy(), fx.DEFAULT],
                          baseline["weight"], w[(frame["period_ym"].str[:4] == year).to_numpy()])
        for year in sorted(frame["period_ym"].str[:4].unique())
    }  # fmt: skip
    if horizon is not None and len(horizon):
        out["auc_by_horizon"] = horizon_from_start(model, horizon, start)
    sample = frame.head(REASON_CHECK_ROWS)
    try:
        feats = families.feature_list(model)
        codes = reasons.top_reasons(
            model.contributions(inputs(model, sample)), inputs(model, sample), features=feats
        )
        out["reason_codes"] = bool(codes) and all(isinstance(c, list) for c in codes)
        out["reason_examples"] = codes[:5]
    except Exception as e:  # noqa: BLE001 - a family that can't explain itself fails ADR-009, it doesn't crash
        out["reason_codes"] = False
        out["reason_error"] = f"{type(e).__name__}: {e}"
    return out


def scalar_metrics(result: dict[str, Any], prefix: str) -> dict[str, float]:
    keys = (
        "log_loss",
        "log_loss_default",
        "log_loss_prepay",
        "auc_default",
        "auc_prepay",
        "gini_default",
        "gini_prepay",
    )
    out = {f"{prefix}_{k}": float(result[k]) for k in keys}
    out[f"{prefix}_calibration_passes"] = float(result["calibration_passes"])
    out[f"{prefix}_reason_codes"] = float(result["reason_codes"])
    for h, v in (result.get("auc_by_horizon") or {}).items():
        out[f"{prefix}_auc_{h}m"] = float(v["auc"])
        out[f"{prefix}_gini_{h}m"] = metrics.gini(float(v["auc"]))
    return out


class PDModel:
    """MLflow pyfunc wrapper: predict returns p_none, p_default, p_prepay per row."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def predict(self, context: Any, model_input: Any, params: Any = None) -> Any:  # noqa: ARG002
        import pandas as pd

        p = self.model.predict_proba(inputs(self.model, model_input))
        return pd.DataFrame(p, columns=["p_none", "p_default", "p_prepay"])


def log_run(mlflow: Any, name: str, model: Any, tuning: dict[str, Any], result: dict[str, Any], tags: dict[str, str],
            extra_params: dict[str, Any], example: Any, curve_data: dict[str, Any] | None = None) -> str:  # fmt: skip
    """Log one family's training and validation to MLflow; returns the run id. `curve_data` (from the
    notebook: LightGBM rounds, a learning curve, the training-vs-validation fit row) is logged as
    `curves.json`, PNG charts and, for LightGBM, per-round metric histories."""
    from mlflow import pyfunc  # noqa: F401 (loads the flavour, so mlflow.pyfunc exists)
    from mlflow.models import infer_signature

    params = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in model.params().items()}
    with mlflow.start_run(run_name=f"{name}-validation") as run:
        mlflow.set_tags(tags | {"family": name, "stage": "validation", "phase": "2"})
        mlflow.log_params({k: str(v)[:500] for k, v in (params | extra_params).items()})
        mlflow.log_metrics(scalar_metrics(result, "val"))
        mlflow.log_dict(result, "validation.json")
        mlflow.log_dict(tuning, "tuning.json")
        mlflow.log_dict({"wording": reasons.wording_table()}, "reason_wording.json")
        if curve_data:
            from cecl.models import curves

            mlflow.log_dict(curve_data, "curves.json")
            if curve_data.get("rounds"):
                curves.log_rounds(mlflow, curve_data["rounds"])
                mlflow.log_figure(
                    curves.round_figure(curve_data["rounds"], name), "curves/boosting_rounds.png"
                )
            if curve_data.get("learning"):
                mlflow.log_figure(
                    curves.learning_figure(curve_data["learning"], name), "curves/learning_curve.png"
                )
            fit = curve_data.get("fit")
            if fit:
                mlflow.log_metrics({f"fit_train_{k}": float(v) for k, v in fit["train"].items()})
                mlflow.set_tag("fit_verdict", curves.verdict(fit["train"], fit["valid"])[:250])
        if hasattr(model, "bins"):
            mlflow.log_dict(
                {b.name: {"edges": b.edges, "rates": b.rates} for b in model.bins}, "scorecard_bins.json"
            )
        feats = families.feature_list(model)
        derived = any(f in fx.DERIVED for f in feats)  # the era is derived from the origination year
        sample_in = example[list(fx.FEATURE_NAMES) + (["vintage_year"] if derived else [])].head(50)
        wrapper = PDModel(model)
        model_args = {"python_model": _PyfuncAdapter(wrapper),
                      "signature": infer_signature(sample_in, wrapper.predict(None, sample_in))}  # fmt: skip
        try:  # MLflow 3 names the model `name`; MLflow 2 (Databricks serverless) uses `artifact_path`
            mlflow.pyfunc.log_model(name="model", **model_args)
        except TypeError:
            mlflow.pyfunc.log_model(artifact_path="model", **model_args)
        return str(run.info.run_id)


class _PyfuncAdapter:  # pragma: no cover - only constructed inside MLflow logging
    """Adapter so MLflow sees a PythonModel subclass without importing mlflow at module import."""

    def __new__(cls, wrapper: PDModel) -> Any:
        import mlflow.pyfunc

        class _Model(mlflow.pyfunc.PythonModel):  # type: ignore[name-defined, unused-ignore]
            def __init__(self, inner: PDModel) -> None:
                self.inner = inner

            def predict(self, context: Any, model_input: Any, params: Any = None) -> Any:
                return self.inner.predict(context, model_input, params)

        return _Model(wrapper)
