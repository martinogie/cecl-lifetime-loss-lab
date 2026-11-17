"""The measures used to compare and test the models (Phase 2, ADR-009), all **weighted**.

Every function takes the sampling weights: each kept no-event month counts for the months it stands
for (about 25), events count once (ingestion/feature_build.py). So every number here describes the
full panel, not the sample (Phase 2 plan; checked in the tests by comparing a weighted sample with
the full table).

Probabilities are an n x 3 array in the order (no event, default, prepayment) = outcomes 0, 1, 2
(cecl/features.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

EPS = 1e-15
CAUSES = {"default": 1, "prepay": 2}
CALIBRATION_LOW, CALIBRATION_HIGH = 0.80, 1.25  # ADR-009 B
MIN_EVENTS_FOR_BAR = 200  # ADR-009 B: the bar applies to years and vintages with at least 200 defaults


def _arrays(*xs: Any) -> list[np.ndarray]:
    return [np.asarray(x, dtype=float) for x in xs]


def log_loss(outcome: Sequence[int], proba: Any, weight: Sequence[float]) -> float:
    """Weighted multiclass log loss: the average of -log(probability given to what happened)."""
    y = np.asarray(outcome, dtype=int)
    p, w = _arrays(proba, weight)
    picked = np.clip(p[np.arange(len(y)), y], EPS, 1.0)
    return float(-(w * np.log(picked)).sum() / w.sum())


def cause_log_loss(outcome: Sequence[int], proba: Any, weight: Sequence[float], cause: str) -> float:
    """Weighted binary log loss for one cause (default, or prepayment) against everything else."""
    y = (np.asarray(outcome, dtype=int) == CAUSES[cause]).astype(float)
    p = np.clip(np.asarray(proba, dtype=float)[:, CAUSES[cause]], EPS, 1 - EPS)
    w = np.asarray(weight, dtype=float)
    return float(-(w * (y * np.log(p) + (1 - y) * np.log(1 - p))).sum() / w.sum())


def auc(label: Sequence[int], score: Sequence[float], weight: Sequence[float] | None = None) -> float:
    """Weighted area under the ROC curve: the chance a random event (weighted) is scored above a
    random non-event; ties count half. NaN if one class is absent."""
    y = np.asarray(label, dtype=bool)
    s = np.asarray(score, dtype=float)
    w = np.ones(len(y)) if weight is None else np.asarray(weight, dtype=float)
    pos, neg = w[y].sum(), w[~y].sum()
    if pos == 0 or neg == 0:
        return float("nan")
    # Vectorised: group equal scores; each positive beats the negatives in lower groups and half of
    # the negatives tied with it (the same result as the pairwise definition, in a few array steps).
    _, group = np.unique(s, return_inverse=True)
    wp = np.bincount(group, weights=w * y)
    wn = np.bincount(group, weights=w * ~y)
    neg_below = np.cumsum(wn) - wn
    total = float((wp * (neg_below + wn / 2)).sum())
    return float(total / (pos * neg))


@dataclass(frozen=True)
class CalibrationRow:
    group: str
    events: float  # actual weighted events
    predicted: float  # sum of weighted probabilities
    ratio: float  # predicted / actual
    applies: bool  # at least MIN_EVENTS_FOR_BAR actual events
    passes: bool  # within the bar (or the bar doesn't apply)


def calibration(groups: Sequence[Any], outcome: Sequence[int], proba: Any, weight: Sequence[float],
                cause: str = "default") -> list[CalibrationRow]:  # fmt: skip
    """Predicted / actual events by group (year or vintage), with the ADR-009 bar."""
    g = np.asarray([str(x) for x in groups])
    y = np.asarray(outcome, dtype=int) == CAUSES[cause]
    p = np.asarray(proba, dtype=float)[:, CAUSES[cause]]
    w = np.asarray(weight, dtype=float)
    rows = []
    for key in sorted(set(g.tolist())):
        m = g == key
        events, predicted = float((w[m] * y[m]).sum()), float((w[m] * p[m]).sum())
        ratio = predicted / events if events else float("inf")
        applies = events >= MIN_EVENTS_FOR_BAR
        passes = (not applies) or CALIBRATION_LOW <= ratio <= CALIBRATION_HIGH
        rows.append(CalibrationRow(key, events, predicted, ratio, applies, passes))
    return rows


def psi(expected: Sequence[float], actual: Sequence[float], w_expected: Sequence[float] | None = None,
        w_actual: Sequence[float] | None = None, bins: int = 10) -> float:  # fmt: skip
    """Population stability index of a score between two periods (bins: deciles of the first)."""
    e, a = _arrays(expected, actual)
    we = np.ones(len(e)) if w_expected is None else np.asarray(w_expected, dtype=float)
    wa = np.ones(len(a)) if w_actual is None else np.asarray(w_actual, dtype=float)
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges, weights=we)[0] / we.sum()
    pa = np.histogram(a, edges, weights=wa)[0] / wa.sum()
    pe, pa = np.clip(pe, 1e-6, None), np.clip(pa, 1e-6, None)
    return float(((pa - pe) * np.log(pa / pe)).sum())


def gini(auc_value: float) -> float:
    """Gini coefficient (accuracy ratio) = 2 x AUC - 1: what bank risk teams usually quote.
    0 = no ranking power, 1 = perfect; NaN stays NaN."""
    return 2.0 * auc_value - 1.0


def horizon_auc(loan_id: Sequence[str], period_ym: Sequence[str], outcome: Sequence[int], pd_month: Sequence[float],
                start: str, months: int) -> tuple[float, int]:  # fmt: skip
    """AUC at a horizon, from a fixed start month (the horizon table: every month of each loan).

    Loans alive in `start` are scored by their cumulative chance of default over the next `months`
    months, 1 - prod(1 - monthly PD), using their actual months (a loan that prepays or leaves
    stops early). The label is whether the loan defaulted within those months. Returns (AUC, loans).
    """
    end = _add_months(start, months - 1)
    ids = np.asarray(loan_id)
    per = np.asarray(period_ym)
    y = np.asarray(outcome, dtype=int)
    pdm = np.asarray(pd_month, dtype=float)
    window = (per >= start) & (per <= end)
    alive_at_start = set(ids[per == start].tolist())
    keep = window & np.isin(ids, list(alive_at_start))
    survival: dict[str, float] = {}
    defaulted: dict[str, bool] = {}
    for i, py, ypd in zip(ids[keep], y[keep], pdm[keep], strict=True):
        survival[i] = survival.get(i, 1.0) * (1.0 - ypd)
        defaulted[i] = defaulted.get(i, False) or py == 1
    loans = sorted(survival)
    score = [1.0 - survival[i] for i in loans]
    label = [int(defaulted[i]) for i in loans]
    return auc(label, score), len(loans)


def _add_months(ym: str, n: int) -> str:
    index = int(ym[:4]) * 12 + int(ym[4:]) - 1 + n
    return f"{index // 12}{index % 12 + 1:02d}"
