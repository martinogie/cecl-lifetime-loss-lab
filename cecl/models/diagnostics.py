"""Calibration diagnostics for the trained families (Phase 2, before any fix; Martins, 17 Nov 2026).

Three questions, answered from the existing models' predictions (every measure weighted):
1. **Delinquency at the start of the month:** AUC and calibration for loans that were current last
   month vs already delinquent. Is the 0.99 AUC driven by loans that are already behind (a loan 60
   days late often reaches 90, the default line, the next month)?
2. **Predicted / actual by calendar year inside the training period (1999-2016):** do the models also
   over-predict 2012-2016 and under-predict the crisis years (a shift over time)?
Also predicted / actual **prepayments** by calendar year (training and validation), not just their AUC.

3. **2017-2018 in the hurricane states (TX, FL, PR) vs the rest:** how much of the 2017-2018
   over-prediction comes from disaster forbearance (flags only from 2014, L-016)?
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cecl import features as fx
from cecl import metrics

HURRICANE_STATES = ("TX", "FL", "PR")
HURRICANE_YEARS = ("2017", "2018")


def _ratio(frame: Any, proba: np.ndarray, mask: np.ndarray, cause: str = "default") -> dict[str, float]:
    y = frame["outcome"].to_numpy()[mask] == metrics.CAUSES[cause]
    w = frame["weight"].to_numpy(dtype=float)[mask]
    p = proba[mask, metrics.CAUSES[cause]]
    events, predicted = float((w * y).sum()), float((w * p).sum())
    return {"events": events, "predicted": predicted, "ratio": predicted / events if events else float("nan"),
            "weighted_months": float(w.sum())}  # fmt: skip


def by_start_status(frame: Any, proba: np.ndarray) -> dict[str, Any]:
    """AUC and calibration for loans current last month vs already delinquent."""
    late = frame["months_late_prev"].fillna(0).to_numpy() > 0
    out = {}
    for label, mask in (("current", ~late), ("delinquent", late)):
        y = frame["outcome"].to_numpy()[mask]
        w = frame["weight"].to_numpy(dtype=float)[mask]
        auc = metrics.auc(y == fx.DEFAULT, proba[mask, fx.DEFAULT], w)
        out[label] = _ratio(frame, proba, mask) | {
            "auc_default": auc,
            "gini_default": metrics.gini(auc),  # Gini = 2 x AUC - 1
            "share_of_defaults": float((w * (y == fx.DEFAULT)).sum()),
        }
    total = out["current"]["share_of_defaults"] + out["delinquent"]["share_of_defaults"]
    for label in out:
        out[label]["share_of_defaults"] = out[label]["share_of_defaults"] / total if total else float("nan")
    return out


def by_calendar_year(frame: Any, proba: np.ndarray, cause: str = "default") -> dict[str, dict[str, float]]:
    years = frame["period_ym"].str[:4].to_numpy()
    return {y: _ratio(frame, proba, years == y, cause) for y in sorted(set(years.tolist()))}


def hurricane_states(frame: Any, proba: np.ndarray, states: Any = None) -> dict[str, dict[str, float]]:
    """2017-2018 predicted / actual defaults in TX, FL and PR against every other state."""
    st = np.asarray(states if states is not None else frame["property_state"].to_numpy())
    in_years = np.isin(frame["period_ym"].str[:4].to_numpy(), HURRICANE_YEARS)
    hit = np.isin(st, HURRICANE_STATES)
    return {"hurricane_states": _ratio(frame, proba, in_years & hit),
            "other_states": _ratio(frame, proba, in_years & ~hit)}  # fmt: skip


def run_all(model: Any, train: Any, valid: Any, inputs: Any) -> dict[str, Any]:
    """All three diagnostics for one model; `inputs(model, frame)` gives the model's inputs
    (cecl.models.pipeline.inputs)."""
    p_train, p_valid = model.predict_proba(inputs(model, train)), model.predict_proba(inputs(model, valid))
    return {
        "start_status_validation": by_start_status(valid, p_valid),
        "start_status_training": by_start_status(train, p_train),
        "calendar_year_training": by_calendar_year(train, p_train),
        "calendar_year_validation": by_calendar_year(valid, p_valid),
        "prepay_by_year_training": by_calendar_year(train, p_train, "prepay"),
        "prepay_by_year_validation": by_calendar_year(valid, p_valid, "prepay"),
        "hurricanes_2017_2018": hurricane_states(valid, p_valid),
    }
