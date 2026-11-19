"""Model v2: tune, train, evaluate and select the transition models (ADR-009 Amendment 3), engine-agnostic.

Two families per step (logistic and LightGBM; the EBM is dropped for v2), three steps: 6 models. The
Databricks notebook (`52_train_transition_v2.py`) reads `transitions_v2` into pandas and calls these
functions; the tests call them on made-up frames. The holdout months (July 2024 - June 2026) are never
in the frames passed here.

- `tune`: each setting is fitted on each walk-forward exam's training months (F1-F4) and scored on its
  exam years by the step's log loss across all destinations; the best **average** wins. The chosen
  setting's four exam models are kept: they are the walk-forward projection's models.
- `exam_results`: every Amendment 1-2 bar (by year, vintage, whole period, late-history band, last
  month's state) on each exam's scored months, for the chosen setting's exam model.
- `lock_band_bar`: the rate-lock band bar, judged in-sample on July 2022 - June 2024 (Amendment 3 B).
- `info_exam`: F5, trained to 2019 and scored on July 2022 - June 2024; reported only.
- `screen_stress`: the household-stress keep rule (Amendment 3), on the current-loan logistic model.
- `select_step`: the 2% rule per step on the average exam log loss, bars and reason codes required,
  ties to logistic.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np

from cecl import features as fx
from cecl import metrics
from cecl.models import transition_pipeline as tp
from cecl.models import transitions

FAMILIES = ("logistic", "lightgbm")  # EBM dropped for v2 (Martins, 19 November 2026)
SIMPLICITY_ORDER = ("logistic", "lightgbm")
GRID = {f: tp.GRID[f] for f in FAMILIES}
GAIN_TO_SWITCH = 0.02  # the 2% rule (ADR-009 C)
STRESS_MIN_GAIN = 0.002  # keep rule: at least 0.2% better on average over F1-F4 ...
STRESS_MAX_FOLD_LOSS = 0.001  # ... and no exam more than 0.1% worse
STRESS_SCREEN_SETTINGS = {"C": 0.1}  # v1's chosen setting for the current-loan logistic model
LOCK_BANDS = ((-99.0, -2.0, "2+ below"), (-2.0, -1.0, "1-2 below"), (-1.0, 0.0, "0-1 below"),
              (0.0, 1.0, "0-1 above"), (1.0, 99.0, "1+ above"))  # fmt: skip
IN_SAMPLE_LOCK = ("202207", "202406")


def add_derived(frame: Any, features: tuple[str, ...]) -> Any:
    """The derived v2 inputs (rate lock, ever missed) as columns, so v1's helpers work unchanged."""
    out = frame.copy()
    for name in features:
        if name in fx.V2_DERIVED and name not in out.columns:
            out[name] = fx.V2_DERIVED[name]({c: out[c].to_numpy() for c in ("rate_incentive", "loan_age",
                                                                              "months_since_first_miss")})  # fmt: skip
    return out


def period(frame: Any) -> np.ndarray:
    return frame["period_ym"].to_numpy().astype(str)


def exam_frames(frame: Any, fold: tuple[str, str, str]) -> tuple[Any, Any]:
    """(training rows up to the exam's cut, the exam's scored rows)."""
    p = period(frame)
    last_train, first, last = fold
    return frame[p <= last_train], frame[(p >= first) & (p <= last)]


def fit(family: str, start: int, frame: Any, settings: dict[str, Any], features: tuple[str, ...]) -> Any:
    return tp.fit(family, start, frame, settings, features=features)


def tune(family: str, start: int, frame: Any, features: tuple[str, ...],
         grid: list[dict[str, Any]] | None = None, folds: tuple = fx.V2_FOLDS) -> tuple[dict[str, Any], list[Any]]:  # fmt: skip
    """Walk-forward tuning over F1-F4; returns (record, the chosen setting's exam models)."""
    grid = GRID[family] if grid is None else grid
    record: dict[str, Any] = {"grid": grid, "folds": [list(f) for f in folds], "scores": [], "average": []}
    best, best_models = float("inf"), []
    for settings in grid:
        per_fold, models = [], []
        for fold in folds:
            train, scored = exam_frames(frame, fold)
            model = fit(family, start, train, settings, features)
            per_fold.append(tp.step_log_loss(model, scored))
            models.append(model)
        avg = float(np.mean(per_fold))
        record["scores"].append(per_fold)
        record["average"].append(avg)
        if avg < best:  # keep only the best setting's exam models (memory)
            best, best_models = avg, models
    record["chosen"] = grid[int(np.argmin(record["average"]))]
    record["chosen_average"] = best
    return record, best_models


def exam_results(models: list[Any], frame: Any, folds: tuple = fx.V2_FOLDS,
                 names: tuple[str, ...] = fx.V2_FOLD_NAMES) -> list[dict[str, Any]]:  # fmt: skip
    """Every Amendment 1-2 measure on each exam's scored months (its own exam model)."""
    out = []
    for model, fold, name in zip(models, folds, names, strict=True):
        train, scored = exam_frames(frame, fold)
        r = tp.evaluate(model, scored, train, period_label=name)
        r["exam"] = name
        out.append(r)
    return out


def lock_band(rate_incentive: Any) -> np.ndarray:
    x = np.asarray(rate_incentive, dtype=float)
    out = np.full(len(x), "missing", dtype=object)
    for lo, hi, name in LOCK_BANDS:
        out[(x > lo) & (x <= hi)] = name
    return out


def lock_band_bar(model: Any, frame: Any, window: tuple[str, str] = IN_SAMPLE_LOCK) -> dict[str, Any]:
    """Calibration of every move by rate-lock band on July 2022 - June 2024 (in-sample, judged; cells
    with at least 200 actual moves), Amendment 3 B."""
    p_ = period(frame)
    f = frame[(p_ >= window[0]) & (p_ <= window[1])]
    out: dict[str, Any] = {
        "window": list(window),
        "rows": int(len(f)),
        "in_sample": True,
        "cells": {},
        "passes": True,
    }
    if not len(f):
        return out
    y, w = f["state"].to_numpy(dtype=int), f["weight"].to_numpy(dtype=float)
    p = model.predict_proba(tp.columns(f, transitions.feature_list(model)))
    bands = lock_band(f["rate_incentive"])
    for dest in range(transitions.N_STATES):
        if dest == model.start:
            continue
        rows = metrics.calibration_events(bands, y == dest, p[:, dest], w)
        out["cells"][fx.STATES[dest]] = [asdict(r) for r in rows]
        out["passes"] = out["passes"] and all(r.passes for r in rows)
    return out


def info_exam(family: str, start: int, frame: Any, settings: dict[str, Any], features: tuple[str, ...],
              fold: tuple[str, str, str] = fx.V2_INFO_FOLD) -> tuple[Any, dict[str, Any]]:  # fmt: skip
    """F5 (information only): trained to December 2019, scored on July 2022 - June 2024."""
    train, scored = exam_frames(frame, fold)
    model = fit(family, start, train, settings, features)
    r = tp.evaluate(model, scored, train, period_label="F5 2022-2024 (information)") if len(scored) else {}
    return model, r


def screen_stress(frame: Any, candidates: tuple[str, ...], base: tuple[str, ...] = fx.V2_BASE_FEATURES,
                  start: int = fx.CURRENT, settings: dict[str, Any] | None = None,
                  folds: tuple = fx.V2_FOLDS) -> dict[str, Any]:  # fmt: skip
    """The keep rule: each candidate added alone to the current-loan logistic model; kept if the average
    exam log loss improves by at least 0.2%, no exam worsens by more than 0.1%, and it is eligible (has
    a Fed scenario path). The sign is held to its business direction by the scorecard bins (a candidate
    whose data runs the other way collapses to one bin and adds nothing)."""
    settings = settings or STRESS_SCREEN_SETTINGS

    def scores(features: tuple[str, ...]) -> tuple[list[float], list[Any]]:
        out, models = [], []
        for fold in folds:
            train, scored = exam_frames(frame, fold)
            model = fit("logistic", start, train, settings, features)
            out.append(tp.step_log_loss(model, scored))
            models.append(model)
        return out, models

    base_scores, _ = scores(base)
    record: dict[str, Any] = {"base": base_scores, "base_average": float(np.mean(base_scores)), "candidates": {},
                              "rule": {"min_average_gain": STRESS_MIN_GAIN, "max_exam_loss": STRESS_MAX_FOLD_LOSS,
                                       "eligible": list(fx.V2_STRESS_ELIGIBLE)}}  # fmt: skip
    for c in candidates:
        s, models = scores(base + (c,))
        gain = 1.0 - float(np.mean(s)) / record["base_average"]
        worst = max(si / bi - 1.0 for si, bi in zip(s, base_scores, strict=True))
        bins = next((b for b in models[-1].bins if b.name == c), None)
        record["candidates"][c] = {
            "scores": s, "average": float(np.mean(s)), "average_gain": gain, "worst_exam_loss": worst,
            "eligible": c in fx.V2_STRESS_ELIGIBLE,
            "bins_last_exam": {"edges": bins.edges, "rates": bins.rates} if bins else None,
            "kept": bool(c in fx.V2_STRESS_ELIGIBLE and gain >= STRESS_MIN_GAIN and worst <= STRESS_MAX_FOLD_LOSS
                         and bins is not None and len(bins.edges) > 0),
        }  # fmt: skip
    record["kept"] = [c for c, r in record["candidates"].items() if r["kept"]]
    return record


def bars_pass(result: dict[str, Any]) -> bool:
    """A model passes if every exam's bars pass and the in-sample rate-lock band bar passes."""
    return all(e["calibration_passes"] for e in result["exams"]) and result["lock_band"]["passes"]


def select_step(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The 2% rule for one step. `results[family]` holds `average` (the chosen setting's average exam log
    loss), `exams`, `lock_band` and `reason_codes`. Logistic stays unless LightGBM is at least 2% better,
    passes every bar and gives reason codes; if logistic itself fails a bar and LightGBM does not
    qualify, logistic is kept and its failures recorded."""
    base = results["logistic"]["average"]
    table, qualified = [], []
    for family in SIMPLICITY_ORDER:
        r = results.get(family)
        if r is None:
            continue
        gain = 1.0 - r["average"] / base
        row = {"family": family, "average_log_loss": r["average"], "gain_over_logistic": gain,
               "bars_pass": bars_pass(r), "reason_codes": bool(r["reason_codes"]),
               "failing_cells": failing_cells(r)}  # fmt: skip
        table.append(row)
        if family != "logistic" and gain >= GAIN_TO_SWITCH and row["bars_pass"] and row["reason_codes"]:
            qualified.append(row)
    winner = "logistic"
    if qualified:
        winner = qualified[0]["family"]  # only LightGBM can qualify in v2
    why = ("LightGBM is at least 2% better on the exams, passes every bar and gives reason codes"
           if winner == "lightgbm" else "LightGBM does not qualify (2% rule, bars or reason codes)")  # fmt: skip
    return {"winner": winner, "why": why, "table": table}


def failing_cells(result: dict[str, Any]) -> list[str]:
    out = []
    for e in result["exams"]:
        for key, rows in e["calibration"].items():
            out += [f"{e['exam']}: {key} {r['group']} {r['ratio']:.2f}" for r in rows if not r["passes"]]
        for name, m in e["moves"].items():
            whole = m["whole_period"]
            if name != e["start_state"] and not whole["passes"] and whole["applies"]:
                out.append(f"{e['exam']}: {name} whole period {whole['ratio']:.2f}")
    for dest, rows in result["lock_band"]["cells"].items():
        out += [
            f"in-sample 2022-2024: {dest} by lock band {r['group']} {r['ratio']:.2f}"
            for r in rows
            if not r["passes"]
        ]
    return out


# ---------------------------------------------------------------------------- MLflow
V1_BENCHMARK_SETTINGS = {
    fx.CURRENT: {"C": 0.1},
    fx.LATE_30: {"C": 0.1},
    fx.LATE_60: {"C": 1.0},
}  # v1's locked set


def log_run(mlflow: Any, final: Any, record: dict[str, Any], tags: dict[str, str], params: dict[str, Any],
            artifacts: dict[str, Any], models: dict[str, Any], example: Any, metrics_: dict[str, float]) -> str:  # fmt: skip
    """One v2 training run: tags, parameters, JSON results, every exam model (pickled) and the final model
    as a pyfunc. Returns the run id."""
    import json
    import os
    import pickle
    import tempfile

    from mlflow import pyfunc  # noqa: F401 (loads the flavour)
    from mlflow.models import infer_signature

    from cecl.models.pipeline import _PyfuncAdapter

    start = fx.STATES[final.start]
    with mlflow.start_run(run_name=f"v2-T-{start}-{final.name}") as run:
        mlflow.set_tags(tags | {"model_version": "v2", "family": final.name, "transition": start,
                                "design": "multi-state (ADR-011)", "rules": "ADR-009 Amendment 3"})  # fmt: skip
        flat = {
            k: json.dumps(v) if isinstance(v, (dict, list, tuple)) else v
            for k, v in (final.params() | params).items()
        }
        mlflow.log_params({k: str(v)[:500] for k, v in flat.items()})
        mlflow.log_metrics({k: float(v) for k, v in metrics_.items() if v == v})
        mlflow.log_dict(record, "tuning.json")
        for name, obj in artifacts.items():
            mlflow.log_dict(obj, f"{name}.json")
        with tempfile.TemporaryDirectory() as tmp:
            for name, model in models.items():
                path = os.path.join(tmp, f"{name}.pkl")
                with open(path, "wb") as fh:
                    pickle.dump(model, fh)
                mlflow.log_artifact(path, "exam_models")
        sample_in = example[list(transitions.feature_list(final))].head(50)
        wrapper = tp.TransitionPyfunc(final)
        args = {"python_model": _PyfuncAdapter(wrapper),  # type: ignore[arg-type]
                "signature": infer_signature(sample_in, wrapper.predict(None, sample_in))}  # fmt: skip
        try:
            mlflow.pyfunc.log_model(name="model", **args)
        except TypeError:
            mlflow.pyfunc.log_model(artifact_path="model", **args)
        return str(run.info.run_id)


# ---------------------------------------------------------------------------- walk-forward requirement
EXAM_STARTS = {"F1": ("200801", 36), "F2": ("201101", 36), "F3": ("201401", 36), "F4": ("201701", 36),
               "F5": ("202207", 24)}  # fmt: skip
JUDGED_EXAMS = ("F2", "F3", "F4")  # total and each year within 0.80-1.25
CRISIS_EXAM = "F1"  # closer to actual defaults than the v1-style benchmark
INFO_EXAMS = ("F5",)


def in_bar(ratio: float) -> bool:
    return metrics.CALIBRATION_LOW <= ratio <= metrics.CALIBRATION_HIGH


def default_ratio(check: Any) -> float:
    actual = check.totals["default_actual"]
    return check.totals["default_projected"] / actual if actual else float("inf")


def exam_verdict(check: Any) -> dict[str, Any]:
    """F2-F4 (Amendment 3): total projected defaults within 0.80-1.25, and every default cell by year with
    at least 200 actual defaults within 0.80-1.25. Vintage cells are reported, not judged."""
    years = [c for c in check.cells if c.event == "default" and c.by == "year"]
    failing = [f"year {c.group} {c.ratio:.2f}" for c in years if c.applies and not c.passes]
    total = default_ratio(check)
    if not in_bar(total):
        failing.insert(0, f"total {total:.2f}")
    return {"total_ratio": total, "years": {c.group: {"actual": c.actual, "projected": c.projected,
                                                      "ratio": c.ratio, "judged": c.applies} for c in years},
            "failing": failing, "passes": not failing}  # fmt: skip


def crisis_verdict(v2_check: Any, benchmark_check: Any) -> dict[str, Any]:
    """F1: passes if v2's total projected defaults are closer to actual than the v1-style benchmark's."""
    r2, rb = default_ratio(v2_check), default_ratio(benchmark_check)
    return {"v2_ratio": r2, "benchmark_ratio": rb, "passes": abs(r2 - 1.0) < abs(rb - 1.0)}
