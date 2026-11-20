"""Model v2's projection: a path simulation (ADR-009 Amendment 3, plan decision D1; fixes L-026's cause).

v1's projection (`multistate.py`, unchanged) carries each loan's probability split into slices, which
approximates its history inputs (L-026). v2 adds two more history inputs (worst state in 6 months,
months since the first missed payment), so v2 plays each loan forward along `paths` random paths
instead, and computes every history input **exactly** on each path:

- each month, every live path is scored by the transition model of its state;
- the chance of default and prepayment that month is added to the loan's projection, averaged over its
  paths (so the expected counts are exact for the paths' states; only the paths themselves are random);
- the path then moves to one next state, drawn from those chances (a fixed seed: the same projection
  every run). Paths that default or prepay stop.

History each path carries (the 12 months before the projected month): its states (0, 1, 2) and pauses,
and the months since its first missed payment. At the start these come from the loan's own panel rows
(`ingestion.feature_build.v2_projection_history_sql`); the start row's own inputs are checked against
them (`check_history`, should be 0 mismatches). Future payment pauses are not known: none after the start
(as v1). The economy follows `multistate.Macro` (actual paths for validation; Phase 4 swaps in the
scenarios) plus the national household-stress series.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from cecl import features as fx
from cecl.models import multistate

LIVE = fx.LIVE_STATES
H = 12  # months of history carried
STATE_HIST = tuple(f"state_hist_{j}" for j in range(1, H + 1))
PAUSE_HIST = tuple(f"pause_hist_{j}" for j in range(1, H + 1))
PATH_INPUTS = ("state_last_month", "late_months_12", "worst_state_6", "paused_12", "forbearance_prev",
               "months_since_first_miss")  # fmt: skip
STRESS = tuple(f.name for f in fx.V2_STRESS)


@dataclass
class Household:
    """National household-stress series by YYYYMM (columns: the V2_STRESS variables)."""

    table: Any  # pandas DataFrame, index YYYYMM

    @classmethod
    def from_table(cls, household: Any) -> Household:
        import pandas as pd

        h = household.copy()
        h["ym"] = pd.to_datetime(h["month"]).dt.strftime("%Y%m")
        return cls(h.pivot_table(index="ym", columns="variable", values="value"))

    def value(self, variable: str, ym: str, n: int) -> np.ndarray:
        if ym not in self.table.index or variable not in self.table.columns:
            return np.full(n, np.nan)
        return np.full(n, float(self.table.loc[ym, variable]))


def path_inputs(states: np.ndarray, pauses: np.ndarray, first_miss: np.ndarray) -> dict[str, np.ndarray]:
    """The history inputs of each path from its buffers: `states[:, j-1]` and `pauses[:, j-1]` are the
    state and pause flag j months before the projected month (j = 1 is the starting state)."""
    return {
        "state_last_month": states[:, 1].astype(float),
        "late_months_12": (states[:, :H] >= 1).sum(axis=1).astype(float),
        "worst_state_6": states[:, 1:7].max(axis=1).astype(float),  # months t-7 to t-2
        "paused_12": pauses[:, :H].max(axis=1).astype(float),
        "forbearance_prev": pauses[:, 0].astype(float),
        "months_since_first_miss": first_miss.astype(float),
    }


HISTORY_CODES = 3 * 13 * 3 * 2 * 2 * 61  # distinct values of the six history inputs together


def history_code(hist: dict[str, np.ndarray], rows: np.ndarray) -> np.ndarray:
    """One whole number per path for its six history inputs (each a small whole number)."""
    code = hist["state_last_month"][rows].astype(np.int64)
    for name, size in (("late_months_12", 13), ("worst_state_6", 3), ("paused_12", 2), ("forbearance_prev", 2),
                       ("months_since_first_miss", 61)):  # fmt: skip
        code = code * size + hist[name][rows].astype(np.int64)
    return code


def start_buffers(start: Any, history: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per loan: the state and pause buffers and months since the first miss at the start month."""
    h = history.set_index("loan_id").reindex(start["loan_id"])
    states = np.nan_to_num(h[list(STATE_HIST)].to_numpy(dtype=float), nan=0.0).astype(np.int8)
    pauses = np.nan_to_num(h[list(PAUSE_HIST)].to_numpy(dtype=float), nan=0.0).astype(np.int8)
    first = np.nan_to_num(start["months_since_first_miss"].to_numpy(dtype=float), nan=0.0)
    return states, pauses, first


def check_history(start: Any, history: Any) -> dict[str, int]:
    """Start-row inputs that differ from the same inputs rebuilt from the history (should all be 0)."""
    states, pauses, first = start_buffers(start, history)
    rebuilt = path_inputs(states, pauses, first)
    out = {"starting_state": int((states[:, 0] != start["state_prev"].to_numpy(dtype=int)).sum())}
    for name in ("state_last_month", "late_months_12", "worst_state_6", "paused_12", "forbearance_prev"):
        if name in start:
            out[name] = int((rebuilt[name] != np.nan_to_num(start[name].to_numpy(dtype=float))).sum())
    return out


def stress_inputs(hh: Household | None, start_ym: str, k: int, n: int, features: tuple[str, ...]) -> dict[str, np.ndarray]:  # fmt: skip
    prev = multistate.add_months(start_ym, k - 1)
    return {f: hh.value(f, prev, n) for f in features if f in STRESS and hh is not None}


def project(models: dict[int, Any], start: Any, history: Any, macro: multistate.Macro, start_ym: str,
            months: int, household: Household | None = None, paths: int = 8, seed: int = 7,
            observer: Any = None, first_state: Any = None, first_pause: Any = None) -> multistate.Projection:  # fmt: skip
    """Play every loan in `start` forward `months` months along `paths` random paths each, with one
    transition model per live state (`models[state].predict_proba(inputs)` -> n x 5).

    `observer` (diagnostics only; it changes nothing): called each month with
    `(k, state, alive, hist, loan, probs)`, where `probs[s]` is the n_rows x 5 matrix scored for the live
    paths in state `s` and `hist` holds every path's history inputs that month.

    `first_state` (the reserve engine): each loan's **actual** state at the end of month 0 (0, 1 or 2; the
    loan is alive). Month 0 is then not projected (its chances stay 0): every path starts month 1 from the
    known state, with month 0's actual payment-pause flag `first_pause` (0 if not given) in its history.
    So a reserve at the end of month 0 projects only the months after it, from what is known on that date."""
    rng = np.random.default_rng(seed)
    n = len(start)
    loan = np.repeat(np.arange(n), paths)
    states0, pauses0, first0 = start_buffers(start, history)
    states, pauses = np.repeat(states0, paths, axis=0), np.repeat(pauses0, paths, axis=0)
    first = np.repeat(first0, paths)
    state = np.repeat(start["state_prev"].to_numpy(dtype=np.int8), paths)
    states[:, 0] = state  # the starting state is the month before the start (j = 1)
    alive = np.ones(n * paths, dtype=bool)
    p_def, p_pre = np.zeros((n, months)), np.zeros((n, months))
    feature_sets = {s: tuple(getattr(models[s], "features", fx.V2_BASE_FEATURES)) for s in LIVE}
    every = tuple(dict.fromkeys(f for fs in feature_sets.values() for f in fs))
    stored = tuple(f for f in every if f in start.columns and f not in PATH_INPUTS)
    for k in range(months):
        X_loan = multistate.month_inputs(start, macro, start_ym, k, stored)
        if k > 0:
            X_loan |= stress_inputs(household, start_ym, k, n, every)
        hist = path_inputs(states, pauses, first)
        new_state = state.copy()
        probs: dict[int, np.ndarray] = {}
        known = first_state is not None and k == 0
        if known:
            new_state = np.repeat(np.asarray(first_state, dtype=np.int8), paths)
        for s in LIVE if not known else ():
            rows = np.flatnonzero(alive & (state == s))
            if not len(rows):
                continue
            # Paths of one loan with the same history have identical inputs (most paths stay current), so
            # each distinct (loan, history) is scored once and shared: the same numbers, far less scoring.
            _, back = np.unique(loan[rows] * HISTORY_CODES + history_code(hist, rows), return_inverse=True)
            first_row = rows[np.unique(back, return_index=True)[1]]
            X = {f: X_loan[f][loan[first_row]] for f in stored if f in X_loan}
            X |= {f: hist[f][first_row] for f in PATH_INPUTS}
            X = fx.with_derived(X, feature_sets[s])
            P = models[s].predict_proba({f: X[f] for f in feature_sets[s]})[back.ravel()]
            probs[s] = P
            p_def[:, k] += np.bincount(loan[rows], P[:, fx.DEFAULTED], minlength=n) / paths
            p_pre[:, k] += np.bincount(loan[rows], P[:, fx.PREPAID], minlength=n) / paths
            u = rng.random(len(rows))
            new_state[rows] = np.minimum((u[:, None] > np.cumsum(P, axis=1)).sum(axis=1), 4)
        if observer is not None:
            observer(k, state, alive, hist, loan, probs)
        # history moves one month on: this month's state becomes "last month"; no future pauses
        missed_now = alive & (new_state >= 1) & (new_state <= 2) & (first == 0)
        first = np.where(first > 0, np.minimum(first + 1, 60), np.where(missed_now, 1, 0))
        alive &= new_state <= 2
        state = new_state
        states = np.column_stack([np.minimum(state, 2), states[:, :-1]]).astype(np.int8)
        newest = np.zeros(len(pauses), dtype=np.int8)
        if known and first_pause is not None:
            newest = np.repeat(np.asarray(first_pause, dtype=np.int8), paths)
        pauses = np.column_stack([newest, pauses[:, :-1]])
    return multistate.Projection(p_def, p_pre, [multistate.add_months(start_ym, k) for k in range(months)])


def v1_history(history: Any) -> Any:
    """v1's start history (late flag of each of the 12 rows before the start) from v2's, so the v1-style
    benchmark can be projected by `multistate.project` from the same tables."""
    out = history[["loan_id"]].copy()
    for j in range(1, H + 1):
        out[f"late_hist_{j}"] = (history[f"state_hist_{j}"].to_numpy(dtype=float) >= 1).astype(float)
    return out
