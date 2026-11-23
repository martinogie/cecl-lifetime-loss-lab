"""Model monitoring (Phase 8): score stability, data freshness, quarterly calibration and the retrain trigger.

Roadmap v2 changes, "Phase 8: drift and performance monitoring" (approved by Martins, 18 November 2026):

- **Weekly (Wednesday):** score the live book with the latest economic data and compare each transition's
  score distribution with its baseline (population stability index, PSI); report each source's freshness;
  give one status (green / amber / red). If no new economic month has arrived since the last run, scoring is
  skipped and only freshness is reported.
- **Quarterly (after each Freddie release):** predicted / actual per transition on the new quarter, by year,
  vintage and late-history band, with the ADR-009 bar (0.80-1.25 where a cell has at least 200 actual events);
  AUC; one trend point per quarter; an alert for every judged cell outside the bar.
- **Retraining** happens only yearly, or when quarterly monitoring finds, two quarters in a row, a judged
  default cell outside 0.80-1.25 or a transition with PSI above 0.25 (`retrain_due`).

Everything here is plain numbers: a baseline is kept as a *profile* (decile edges and shares), never as
loan-level scores, so it can be stored and committed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from cecl.metrics import CALIBRATION_HIGH, CALIBRATION_LOW, MIN_EVENTS_FOR_BAR

PSI_WATCH = 0.10  # amber: a shift worth watching (the usual industry rule of thumb)
PSI_REVIEW = 0.25  # red: a material shift; two quarters in a row triggers a model review (18 Nov 2026)
RETRAIN_DAYS = 365  # yearly retraining at the latest
STATUS_ORDER = ("green", "amber", "red")

# Freshness: how old each source's latest month may be (months before today's month) and still be green;
# one period more is amber, beyond that red. Freddie publishes quarterly with about a 4-6 month lag;
# FRED's unemployment and rates come monthly (one month back); FHFA's monthly index about two months back.
FRESHNESS = {
    "freddie_performance": {"green_months": 7, "period_months": 3},
    "fred_national": {"green_months": 2, "period_months": 1},
    "fred_state_unemployment": {"green_months": 3, "period_months": 1},
    "fhfa_hpi": {"green_months": 3, "period_months": 1},
    "ffiec_call_reports": {"green_months": 5, "period_months": 3},
}


def worst(statuses: Sequence[str]) -> str:
    return max(statuses, key=STATUS_ORDER.index) if statuses else "green"


def months_old(ym: str, today: date) -> int:
    return (today.year * 12 + today.month) - (int(ym[:4]) * 12 + int(ym[4:6]))


# ---------------------------------------------------------------- score stability


def profile(
    scores: Sequence[float], weights: Sequence[float] | None = None, bins: int = 10
) -> dict[str, Any]:
    """A score distribution kept as decile edges (inner edges only) and the share of weight in each bin."""
    s = np.asarray(scores, dtype=float)
    w = np.ones(len(s)) if weights is None else np.asarray(weights, dtype=float)
    if len(s) == 0:
        raise ValueError("no scores to profile")
    inner = np.unique(np.quantile(s, np.linspace(0, 1, bins + 1))[1:-1])
    edges = np.concatenate(([-np.inf], inner, [np.inf]))
    shares = np.histogram(s, edges, weights=w)[0] / w.sum()
    return {"edges": [float(x) for x in inner], "shares": [float(x) for x in shares], "n": int(len(s)),
            "mean": float((s * w).sum() / w.sum())}  # fmt: skip


def psi(base: Mapping[str, Any], scores: Sequence[float], weights: Sequence[float] | None = None) -> float:
    """PSI of `scores` against a stored baseline profile (same formula as cecl.metrics.psi)."""
    s = np.asarray(scores, dtype=float)
    w = np.ones(len(s)) if weights is None else np.asarray(weights, dtype=float)
    edges = np.concatenate(([-np.inf], np.asarray(base["edges"], dtype=float), [np.inf]))
    pe = np.clip(np.asarray(base["shares"], dtype=float), 1e-6, None)
    pa = np.clip(np.histogram(s, edges, weights=w)[0] / w.sum(), 1e-6, None)
    return float(((pa - pe) * np.log(pa / pe)).sum())


def psi_status(value: float) -> str:
    return "red" if value > PSI_REVIEW else "amber" if value > PSI_WATCH else "green"


# ---------------------------------------------------------------- freshness


def freshness(latest: Mapping[str, str | None], today: date) -> dict[str, dict[str, Any]]:
    """Each source's latest month against how old it may be. A missing source is red."""
    out: dict[str, dict[str, Any]] = {}
    for src, rule in FRESHNESS.items():
        ym = latest.get(src)
        if not ym:
            out[src] = {"latest": None, "months_old": None, "status": "red"}
            continue
        age = months_old(ym, today)
        status = ("green" if age <= rule["green_months"]
                  else "amber" if age <= rule["green_months"] + rule["period_months"] else "red")  # fmt: skip
        out[src] = {"latest": ym, "months_old": age, "status": status}
    return out


def weekly_status(fresh: Mapping[str, Mapping[str, Any]], psis: Mapping[str, float] | None) -> dict[str, Any]:
    """One status for the week: the worst of every source's freshness and every transition's PSI."""
    parts = [str(f["status"]) for f in fresh.values()]
    psi_part = {k: psi_status(v) for k, v in (psis or {}).items()}
    status = worst(parts + list(psi_part.values()))
    return {"status": status, "freshness": dict(fresh), "psi": dict(psis or {}), "psi_status": psi_part,
            "scored": psis is not None}  # fmt: skip


def needs_scoring(latest_macro_ym: str | None, last_scored_ym: str | None) -> bool:
    """Score only when a new economic month has arrived since the last scored run (no Databricks minutes
    otherwise)."""
    return bool(latest_macro_ym) and (last_scored_ym is None or str(latest_macro_ym) > str(last_scored_ym))


# ---------------------------------------------------------------- quarterly calibration


@dataclass(frozen=True)
class Cell:
    transition: str  # e.g. "current->default"
    by: str  # "year", "vintage" or "band"
    key: str
    actual: float
    predicted: float

    @property
    def ratio(self) -> float:
        return self.predicted / self.actual if self.actual else float("inf")

    @property
    def judged(self) -> bool:
        return self.actual >= MIN_EVENTS_FOR_BAR

    @property
    def passes(self) -> bool:
        return (not self.judged) or CALIBRATION_LOW <= self.ratio <= CALIBRATION_HIGH

    def as_dict(self) -> dict[str, Any]:
        return {"transition": self.transition, "by": self.by, "key": self.key, "actual": self.actual,
                "predicted": self.predicted, "ratio": self.ratio, "judged": self.judged, "passes": self.passes}  # fmt: skip


def cells(
    transition: str, by: str, groups: Sequence[Any], event: Sequence[bool], prob: Sequence[float]
) -> list[Cell]:
    """Predicted / actual of one transition by one grouping (one month's actual rows, one-step scores)."""
    g = np.asarray([str(x) for x in groups])
    y = np.asarray(event, dtype=bool)
    p = np.asarray(prob, dtype=float)
    return [
        Cell(transition, by, k, float(y[g == k].sum()), float(p[g == k].sum()))
        for k in sorted(set(g.tolist()))
    ]


def is_default_transition(transition: str) -> bool:
    return transition.endswith("->default")


def quarterly_summary(quarter: str, all_cells: Sequence[Cell], aucs: Mapping[str, float],
                      psis: Mapping[str, float] | None = None) -> dict[str, Any]:  # fmt: skip
    """The quarter's monitoring record (one trend point): failed judged cells, the default ones separately,
    AUC per transition and the latest PSI per transition."""
    failed = [c for c in all_cells if c.judged and not c.passes]
    return {"quarter": quarter, "cells": [c.as_dict() for c in all_cells],
            "failed_cells": [c.as_dict() for c in failed],
            "default_cells_failed": sum(1 for c in failed if is_default_transition(c.transition)),
            "auc": dict(aucs), "psi": dict(psis or {})}  # fmt: skip


def alerts(summary: Mapping[str, Any]) -> list[str]:
    """One alert line per judged cell outside the ADR-009 bar."""
    return [f"{c['transition']} by {c['by']} {c['key']}: predicted/actual {c['ratio']:.2f} "
            f"({c['predicted']:.0f} vs {c['actual']:.0f}), outside {CALIBRATION_LOW:.2f}-{CALIBRATION_HIGH:.2f}"
            for c in summary.get("failed_cells", [])]  # fmt: skip


# ---------------------------------------------------------------- retrain trigger


def retrain_due(trained_on: date, today: date, history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The written retraining rule (roadmap v2 changes, approved 18 November 2026): due once a year, or when
    the last two quarterly records **both** have a judged default cell outside 0.80-1.25, or **both** have
    the same transition with PSI above 0.25. Training curves change only when this says so."""
    reasons = []
    if (today - trained_on).days >= RETRAIN_DAYS:
        reasons.append(f"yearly: trained {trained_on.isoformat()}, {(today - trained_on).days} days ago")
    last = sorted(history, key=lambda h: str(h["quarter"]))[-2:]
    if len(last) == 2:
        if all(int(h.get("default_cells_failed", 0)) > 0 for h in last):
            reasons.append(
                f"a judged default cell outside the bar in {last[0]['quarter']} and {last[1]['quarter']}"
            )
        both = set.intersection(
            *({k for k, v in (h.get("psi") or {}).items() if v > PSI_REVIEW} for h in last)
        )
        for t in sorted(both):
            reasons.append(f"PSI above {PSI_REVIEW} for {t} in {last[0]['quarter']} and {last[1]['quarter']}")
    return {"due": bool(reasons), "reasons": reasons}
