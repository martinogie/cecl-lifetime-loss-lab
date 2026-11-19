"""Tune, train, evaluate and log one transition model (ADR-011; ADR-009 Amendment 1), engine-agnostic.

The Databricks notebook (`51_train_transition.py`) reads the transition table into pandas and calls
these functions; the PC's tests call them on small made-up frames. Nothing here reads files or Spark.

- `tune`: walk-forward on the training years only (ADR-009 Amendment 1, tuning): three folds (train
  to 2007 / score 2008-2010; to 2010 / 2011-2013; to 2013 / 2014-2016); each setting is scored by the
  step's own weighted log loss across **all its destinations**; the best average wins. The 2017-2019
  validation years and the test months are never used.
- `train`: tunes (unless settings are fixed), then refits the chosen setting on all of 1999-2016.
- `evaluate`: every ADR-009 Amendment 1 B measure on 2017-2019, weighted: log loss across all
  destinations; per destination log loss, AUC and Gini; calibration by year and by vintage (bar where
  a cell has at least 200 actual moves) plus the whole-period ratio (bar where the move has at least
  200 over the period); PSI of the adverse move's probability by year; the reason-code check.
- `log_run`: everything to MLflow, the model as a pyfunc returning one probability per state.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import numpy as np

from cecl import features as fx
from cecl import metrics
from cecl.models import curves, reasons, transitions

FOLDS = (  # (last training month, first scored month, last scored month)
    ("200712", "200801", "201012"),
    ("201012", "201101", "201312"),
    ("201312", "201401", "201612"),
)
GRID: dict[str, list[dict[str, Any]]] = {
    "logistic": [{"C": 0.1}, {"C": 1.0}],
    "lightgbm": [
        {"num_leaves": nl, "min_child_weight": mcw} for nl in (15, 31, 63) for mcw in (20.0, 50.0, 200.0)
    ],
    "ebm": [{}],  # default settings, not tuned (ADR-009 Amendment 1); its learning curve shows the effect
}
REASON_CHECK_ROWS = 2000
PSI_BASELINE_ROWS = 200_000
TRAIN_SCORE_ROWS = 200_000


def columns(frame: Any, features: tuple[str, ...] = fx.TRANSITION_FEATURES) -> dict[str, np.ndarray]:
    return {f: frame[f].to_numpy(dtype=float) for f in features}


def starting_in(frame: Any, start: int) -> Any:
    """The loan-months that start in one state (each model sees only these)."""
    return frame[frame["state_prev"].to_numpy() == start]


def downsample(frame: Any, every: int, states: tuple[int, ...]) -> Any:
    """A smaller sample for the slowest family (the EBM): 1 in `every` of the rows ending in `states`
    (by a fixed hash), re-weighted per stratum (vintage x year x destination) so every weighted count
    is unchanged; every other row kept. Documented in the run when used."""
    import pandas as pd

    if every <= 1:
        return frame
    h = pd.util.hash_pandas_object(frame[["loan_id", "period_ym"]], index=False).to_numpy() % every
    thin = np.isin(frame["state"].to_numpy(), states)
    stratum = (
        frame["vintage_year"].astype(str)
        + "|"
        + frame["period_ym"].str[:4]
        + "|"
        + frame["state"].astype(str)
    )
    picked = pd.Series((h == 0) & thin).groupby(stratum.to_numpy()).transform("any").to_numpy()
    keep = (~thin) | (h == 0) | ~picked  # a stratum the hash would empty is kept whole
    thin = thin & picked
    out = frame[keep].copy()
    full = frame["weight"].groupby(stratum).sum()
    kept = out["weight"].groupby(stratum[keep]).sum()
    factor = (full / kept).reindex(stratum[keep]).to_numpy()
    out["weight"] = out["weight"].to_numpy() * np.where(thin[keep], factor, 1.0)
    return out


def fit(family: str, start: int, frame: Any, settings: dict[str, Any], eval_frame: Any = None,
        features: tuple[str, ...] = fx.TRANSITION_FEATURES) -> Any:  # fmt: skip
    model = transitions.make(family, start, features=features, **settings)
    eval_set = None
    if eval_frame is not None:
        eval_set = (columns(eval_frame, features), eval_frame["state"].to_numpy(dtype=int),
                    eval_frame["weight"].to_numpy(dtype=float))  # fmt: skip
    return model.fit(columns(frame, features), frame["state"].to_numpy(dtype=int),
                     frame["weight"].to_numpy(dtype=float), eval_set=eval_set)  # fmt: skip


def step_log_loss(model: Any, frame: Any) -> float:
    """The step's weighted log loss across all its destinations (the tuning and selection measure)."""
    p = model.predict_proba(columns(frame, transitions.feature_list(model)))
    return metrics.log_loss(frame["state"].to_numpy(dtype=int), p, frame["weight"].to_numpy(dtype=float))


def tune(family: str, start: int, frame: Any, grid: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Walk-forward tuning on the training years only; returns every fold's score and the choice."""
    grid = GRID[family] if grid is None else grid
    record: dict[str, Any] = {"grid": grid, "folds": [list(f) for f in FOLDS], "scores": [], "average": []}
    if len(grid) == 1:
        record["chosen"] = grid[0]
        return record
    period = frame["period_ym"].to_numpy()
    # A fold with no training or scoring rows (e.g. a slice of recent vintages) is skipped and recorded.
    usable = [f for f in FOLDS if (period <= f[0]).any() and ((period >= f[1]) & (period <= f[2])).any()]
    record["folds_used"] = [list(f) for f in usable]
    if not usable:
        record["chosen"] = grid[0]
        return record
    for settings in grid:
        per_fold = []
        for last_train, first, last in usable:
            model = fit(family, start, frame[period <= last_train], settings)
            per_fold.append(step_log_loss(model, frame[(period >= first) & (period <= last)]))
        record["scores"].append(per_fold)
        record["average"].append(float(np.mean(per_fold)))
    record["chosen"] = grid[int(np.argmin(record["average"]))]
    return record


def train(family: str, start: int, frame: Any, settings: dict[str, Any] | None = None, do_tune: bool = True,
          eval_frame: Any = None) -> tuple[Any, dict[str, Any]]:  # fmt: skip
    """Fit one family for one starting state on the training months; returns (model, tuning record).
    `eval_frame` (validation rows) is only recorded as LightGBM's round-by-round curve."""
    if settings is not None:
        record: dict[str, Any] = {"grid": [settings], "scores": [], "chosen": settings, "fixed": True}
    elif do_tune:
        record = tune(family, start, frame)
    else:
        record = {"grid": [{}], "scores": [], "chosen": {}}
    return fit(family, start, frame, record["chosen"], eval_frame), record


LATE_BANDS = ((0, 0, "0"), (1, 1, "1"), (2, 3, "2-3"), (4, 5, "4-5"), (6, 12, "6+"))


def late_band(late_months_12: Any) -> np.ndarray:
    """The late-payment-history band of each row: months late in the past 12 as 0, 1, 2-3, 4-5, 6+
    (ADR-009 Amendment 2: every move is also judged by band)."""
    x = np.nan_to_num(np.asarray(late_months_12, dtype=float), nan=0.0)
    out = np.full(len(x), "6+", dtype=object)
    for low, high, name in LATE_BANDS:
        out[(x >= low) & (x <= high)] = name
    return out


def last_month_band(state_last_month: Any) -> np.ndarray:
    """Last month's state as a name (current, 30, 60): every move is also judged by it (Martins,
    18 November 2026, extending the ADR-009 Amendment 2 band bar)."""
    x = np.nan_to_num(np.asarray(state_last_month, dtype=float), nan=0.0).astype(int).clip(0, 2)
    return np.array(["current", "30", "60"], dtype=object)[x]


def evaluate(model: Any, frame: Any, train_frame: Any, period_label: str = "2017-2019") -> dict[str, Any]:
    """Every ADR-009 Amendment 1 B measure on one period (validation, or the out-of-time test with
    `period_label` "2022-2026"), weighted, plus the Amendment 2 bar: each move's calibration by
    late-payment-history band."""
    start = model.start
    y = frame["state"].to_numpy(dtype=int)
    w = frame["weight"].to_numpy(dtype=float)
    p = model.predict_proba(columns(frame, transitions.feature_list(model)))
    out: dict[str, Any] = {"start_state": fx.STATES[start], "rows": int(len(frame)), "weighted_months": float(w.sum()),
                           "log_loss": metrics.log_loss(y, p, w), "moves": {}}  # fmt: skip
    groups = {
        "year": frame["period_ym"].str[:4].to_numpy(),
        "vintage": frame["vintage_year"].astype(str).to_numpy(),
        "late_band": late_band(frame["late_months_12"]),
        "state_last_month": last_month_band(frame["state_last_month"]),
    }
    passes = True
    calibration: dict[str, Any] = {}
    for dest in range(transitions.N_STATES):
        name = fx.STATES[dest]
        event = y == dest
        q = np.clip(p[:, dest], metrics.EPS, 1 - metrics.EPS)
        auc = metrics.auc(event, p[:, dest], w) if dest != start else float("nan")
        whole = metrics.calibration_events(np.full(len(y), period_label), event, p[:, dest], w)[0]
        out["moves"][name] = {
            "actual": float((w * event).sum()),
            "predicted": float((w * p[:, dest]).sum()),
            "log_loss": float(-(w * (event * np.log(q) + (~event) * np.log(1 - q))).sum() / w.sum()),
            "auc": auc,
            "gini": metrics.gini(auc),
            "whole_period": asdict(whole),
        }
        if dest == start:  # "stay" is the remainder: reported, not judged on its own
            continue
        passes = passes and whole.passes
        for by, g in groups.items():
            rows = metrics.calibration_events(g, event, p[:, dest], w)
            calibration[f"{name}_by_{by}"] = [asdict(r) for r in rows]
            passes = passes and all(r.passes for r in rows)
    out["calibration"] = calibration
    out["calibration_passes"] = passes
    adverse = fx.ADVERSE[start]
    out["adverse_move"] = fx.STATES[adverse]
    baseline = train_frame.sample(n=min(PSI_BASELINE_ROWS, len(train_frame)), random_state=7)
    p_train = model.predict_proba(columns(baseline, transitions.feature_list(model)))[:, adverse]
    years = frame["period_ym"].str[:4].to_numpy()
    out["psi_by_year"] = {yr: metrics.psi(p_train, p[years == yr, adverse], baseline["weight"], w[years == yr])
                          for yr in sorted(set(years.tolist()))}  # fmt: skip
    sample = frame.head(REASON_CHECK_ROWS)
    try:
        feats = transitions.feature_list(model)
        values = columns(sample, feats)
        codes = reasons.top_reasons(model.contributions(values), values, features=feats)
        out["reason_codes"] = bool(codes) and all(isinstance(c, list) for c in codes)
        out["reason_examples"] = codes[:5]
    except Exception as e:  # noqa: BLE001 - a model that can't explain itself fails ADR-009, it doesn't crash
        out["reason_codes"] = False
        out["reason_error"] = f"{type(e).__name__}: {e}"
    return out


def scalar_metrics(result: dict[str, Any], prefix: str = "val") -> dict[str, float]:
    out = {f"{prefix}_log_loss": float(result["log_loss"]),
           f"{prefix}_calibration_passes": float(result["calibration_passes"]),
           f"{prefix}_reason_codes": float(result["reason_codes"])}  # fmt: skip
    for name, m in result["moves"].items():
        key = name if name[0].isalpha() else f"d{name}"  # metric names can't start with a digit cleanly
        out[f"{prefix}_{key}_log_loss"] = m["log_loss"]
        if m["auc"] == m["auc"]:  # not NaN
            out[f"{prefix}_{key}_auc"] = m["auc"]
            out[f"{prefix}_{key}_gini"] = m["gini"]
        out[f"{prefix}_{key}_ratio"] = m["whole_period"]["ratio"] if m["actual"] else float("nan")
    return {k: v for k, v in out.items() if v == v}


# ---------------------------------------------------------------------------- curves and fit table


def scores(model: Any, frame: Any) -> dict[str, float]:
    """The step's log loss (all destinations) and the adverse move's log loss, AUC and Gini."""
    adverse = fx.ADVERSE[model.start]
    p = model.predict_proba(columns(frame, transitions.feature_list(model)))
    y = frame["state"].to_numpy(dtype=int)
    w = frame["weight"].to_numpy(dtype=float)
    event = y == adverse
    q = np.clip(p[:, adverse], metrics.EPS, 1 - metrics.EPS)
    auc = metrics.auc(event, p[:, adverse], w)
    return {"log_loss": metrics.log_loss(y, p, w),
            "log_loss_adverse": float(-(w * (event * np.log(q) + (~event) * np.log(1 - q))).sum() / w.sum()),
            "auc_adverse": auc, "gini_adverse": metrics.gini(auc)}  # fmt: skip


def train_scores(model: Any, train: Any) -> dict[str, float]:
    return scores(model, train.sample(n=min(TRAIN_SCORE_ROWS, len(train)), random_state=7))


def learning_curve(fit_part: Any, train: Any, valid: Any, fractions: tuple[float, ...] = curves.FRACTIONS,
                   full_model: Any = None) -> list[dict[str, Any]]:  # fmt: skip
    """`fit_part(frame)` returns a fitted model; at 100% the already-trained `full_model` is reused."""
    rows = []
    for f in fractions:
        part = curves.subsample(train, f)
        model = full_model if (f >= 1.0 and full_model is not None) else fit_part(part)
        tr, va = train_scores(model, part), scores(model, valid)
        rows.append({"fraction": f, "rows": int(len(part)),
                     **{f"train_{k}": v for k, v in tr.items()}, **{f"valid_{k}": v for k, v in va.items()}})  # fmt: skip
    return rows


def verdict(train: dict[str, float], valid: dict[str, float]) -> str:
    """Plain English, from the training vs validation step log loss and adverse-move AUC."""
    return curves.verdict({"log_loss_default": train["log_loss"], "auc_default": train["auc_adverse"]},
                          {"log_loss_default": valid["log_loss"], "auc_default": valid["auc_adverse"]})  # fmt: skip


class TransitionPyfunc:
    """MLflow pyfunc wrapper: predict returns p_current, p_30, p_60, p_default, p_prepaid per row."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def predict(self, context: Any, model_input: Any, params: Any = None) -> Any:  # noqa: ARG002
        import pandas as pd

        p = self.model.predict_proba(columns(model_input, transitions.feature_list(self.model)))
        return pd.DataFrame(p, columns=[f"p_{s}" for s in fx.STATES])


def log_run(mlflow: Any, model: Any, tuning: dict[str, Any], result: dict[str, Any], tags: dict[str, str],
            extra_params: dict[str, Any], example: Any, curve_data: dict[str, Any] | None = None) -> str:  # fmt: skip
    """Log one transition model's training and validation to MLflow; returns the run id."""
    from mlflow import pyfunc  # noqa: F401 (loads the flavour, so mlflow.pyfunc exists)
    from mlflow.models import infer_signature

    from cecl.models.pipeline import _PyfuncAdapter

    start = fx.STATES[model.start]
    params = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in model.params().items()}
    with mlflow.start_run(run_name=f"T-{start}-{model.name}-validation") as run:
        mlflow.set_tags(tags | {"family": model.name, "transition": start, "stage": "validation", "phase": "2",
                                "design": "multi-state (ADR-011)"})  # fmt: skip
        mlflow.log_params({k: str(v)[:500] for k, v in (params | extra_params).items()})
        mlflow.log_metrics(scalar_metrics(result))
        mlflow.log_dict(result, "validation.json")
        mlflow.log_dict(tuning, "tuning.json")
        if tuning.get("average"):
            for k, (settings, avg) in enumerate(zip(tuning["grid"], tuning["average"], strict=True)):
                mlflow.log_metric(f"tune_avg_log_loss_{k}", avg)
                mlflow.set_tag(f"tune_setting_{k}", json.dumps(settings))
        mlflow.log_dict({"wording": reasons.wording_table()}, "reason_wording.json")
        if curve_data:
            mlflow.log_dict(curve_data, "curves.json")
            if curve_data.get("rounds"):
                curves.log_rounds(mlflow, curve_data["rounds"])
                mlflow.log_figure(curves.round_figure(curve_data["rounds"], f"T-{start} {model.name}"),
                                  "curves/boosting_rounds.png")  # fmt: skip
            if curve_data.get("learning"):
                mlflow.log_figure(
                    curves.learning_figure(curve_data["learning"], f"T-{start} {model.name}", loss="log_loss",
                                           auc="auc_adverse", loss_label="step log loss, all destinations (weighted)",
                                           auc_label=f"AUC of {result['adverse_move']} (Gini = 2 x AUC - 1)"),
                    "curves/learning_curve.png")  # fmt: skip
            fit_row = curve_data.get("fit")
            if fit_row:
                mlflow.log_metrics({f"fit_train_{k}": float(v) for k, v in fit_row["train"].items()})
                mlflow.set_tag("fit_verdict", verdict(fit_row["train"], fit_row["valid"])[:250])
        if hasattr(model, "bins"):
            mlflow.log_dict(
                {b.name: {"edges": b.edges, "rates": b.rates} for b in model.bins}, "scorecard_bins.json"
            )
        sample_in = example[list(transitions.feature_list(model))].head(50)
        wrapper = TransitionPyfunc(model)
        model_args = {"python_model": _PyfuncAdapter(wrapper),  # type: ignore[arg-type]
                      "signature": infer_signature(sample_in, wrapper.predict(None, sample_in))}  # fmt: skip
        try:  # MLflow 3 names the model `name`; MLflow 2 (Databricks serverless) uses `artifact_path`
            mlflow.pyfunc.log_model(name="model", **model_args)
        except TypeError:
            mlflow.pyfunc.log_model(artifact_path="model", **model_args)
        return str(run.info.run_id)
