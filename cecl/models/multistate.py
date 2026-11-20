"""The multi-state projection (ADR-011): play loans forward month by month from their starting state.

Each loan starts as a probability vector over the live states (current, 30, 60): all of it on its
state in the start month. Every month, each live state's transition model gives the chances of the
five next states; mass that reaches default or prepaid leaves for good (absorbing), the rest is
carried to next month. The result per loan and month is the chance of defaulting and of prepaying in
that month; summed, it gives projected defaults and prepayments by year and by vintage, which the
projection check compares with what actually happened (ADR-009 Amendment 1 B and E).

What moves each month (the economy "as it actually happened", for validation; Phase 4 swaps in the
reserve's forecast and reversion paths):
- loan age and calendar month advance;
- the balance follows the loan's amortisation schedule (rate and remaining term at the start);
- current LTV uses that balance and the state house-price index of the month before;
- state unemployment and its 12-month change, and the rate incentive (loan rate minus the market rate
  of the month before), follow the actual monthly paths;
- the payment-pause flag is known only for the first month (future pauses are not known: 0 after);
- "months late in the past 12" follows the projection as a whole number, as in the data. Each loan's
  probability is split into slices by live state and by months since its last projected late month
  (1 to 11, or 12+ = none in the past year), and each slice carries its own count of projected late
  months. A slice's input is that count plus the loan's actual late months before the start that are
  still inside the 12-month window (from its 12 panel rows before the start month, `late_hist_1` to
  `late_hist_12`; a start month leaves the window exactly when it would in the data), rounded to a
  whole month, plus this month's own state (a loan in 30 has at least one late month, a loan in 60 at
  least two). A loan that has not been late in the projection sees only its own start history, so a
  clean loan sees 0, never a fraction of a late month: the models read this input in steps (the current-loan model splits it at 0, where the roll
  rate to 30 changes 27-fold), so an average such as 0.02 would put every clean loan in the "has been
  late" step (found by the first projection check, 18 November 2026). The start history was first
  faded evenly over 12 months; loans late at the start were then projected at 0.66-0.67 of their actual
  defaults, so the exact history replaced it (18 November 2026). Projected late months older than
  12 months drop out only when a slice reaches 12 months without one. An approximation (limitations
  log L-026), tested by the projection check itself. A loan's own state in the month before the start
  is in its start history only; it is not added again as a projected late month (an earlier version
  counted it twice; found 18 November 2026, effect under 1%).
- "state last month" (the state the month before the starting state) follows the projection exactly:
  the slices also record where the probability came from. For a loan now current, slice 1 means it
  was 30 days late last month and slice FROM_60 that it was 60 days late (both: one month since the
  last late month); for a loan now 30 or 60 days late, the slice is last month's state (0, 1, 2).
- home-price gain since origination follows the state house-price index; SATO is fixed at origination.

Censored loans (they left the data with neither event) are projected only for the months they were
observed, so projected and actual counts cover the same loan-months.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from cecl import features as fx
from cecl import metrics

LIVE = fx.LIVE_STATES
TINY = 1e-12  # probability mass below this in a slice is not scored (saves time, changes nothing)
CLEAN = 12  # slice index (current): no projected late month in the past 12 months
FROM_60 = 13  # slice index (current): 60 days late last month (slice 1: 30 days late last month)
# Slices: for a current loan, months since its last projected late month (1..11, 12 = clean, FROM_60 =
# one month since, coming from 60); for a loan 30 or 60 days late, last month's state (0, 1, 2).
SLICES = 14
HISTORY = tuple(
    f"late_hist_{j}" for j in range(1, 13)
)  # start table: late flag of each of the 12 rows before


def add_months(ym: str, n: int) -> str:
    return metrics._add_months(ym, n)


def months_between(first: str, last: str) -> int:
    """Months from `first` to `last`, inclusive (both YYYYMM)."""
    return (int(last[:4]) * 12 + int(last[4:])) - (int(first[:4]) * 12 + int(first[4:])) + 1


@dataclass
class Macro:
    """The monthly economic paths, indexed by YYYYMM (states as columns)."""

    hpi: Any  # pandas DataFrame: index YYYYMM, one column per state
    unemployment: Any  # pandas DataFrame: index YYYYMM, one column per state
    mortgage_rate: Any  # pandas Series: index YYYYMM

    @classmethod
    def from_tables(cls, state: Any, national: Any) -> Macro:
        """From silver's macro_state_monthly (state, month, variable, value) and
        macro_national_monthly (month, variable, value)."""
        import pandas as pd

        s = state.copy()
        s["ym"] = pd.to_datetime(s["month"]).dt.strftime("%Y%m")
        n = national.copy()
        n["ym"] = pd.to_datetime(n["month"]).dt.strftime("%Y%m")

        def pivot(variable: str) -> Any:
            return s[s["variable"] == variable].pivot_table(index="ym", columns="state", values="value")

        rate = n[n["variable"] == "mortgage_rate_30y"].set_index("ym")["value"].astype(float)
        return cls(pivot("hpi"), pivot("unemployment_rate"), rate)

    def by_state(self, table: Any, ym: str, states: np.ndarray) -> np.ndarray:
        if ym not in table.index:
            return np.full(len(states), np.nan)
        return table.loc[ym].reindex(states).to_numpy(dtype=float)

    def national(self, ym: str, n: int) -> np.ndarray:
        return np.full(n, float(self.mortgage_rate[ym]) if ym in self.mortgage_rate.index else np.nan)


def scheduled_balance(upb: np.ndarray, rate_pct: np.ndarray, remaining: np.ndarray, k: int) -> np.ndarray:
    """Balance after k scheduled monthly payments (annuity), from the start balance, note rate (% a
    year) and remaining term in months; 0 once the term is over."""
    upb, n = np.asarray(upb, dtype=float), np.asarray(remaining, dtype=float)
    r = np.asarray(rate_pct, dtype=float) / 1200.0
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        growth = (1.0 + r) ** k
        payment = np.where(r > 0, upb * r / (1.0 - (1.0 + r) ** -np.maximum(n, 1)), upb / np.maximum(n, 1))
        bal = np.where(
            r > 0, upb * growth - payment * (growth - 1.0) / np.where(r > 0, r, 1.0), upb - payment * k
        )
    return np.where(k >= n, 0.0, np.clip(bal, 0.0, None))


def month_inputs(
    start: Any, macro: Macro, start_ym: str, k: int, features: tuple[str, ...]
) -> dict[str, np.ndarray]:
    """The inputs for projected month k (k = 0 is the start month: the start row's own values)."""
    base = {f: start[f].to_numpy(dtype=float) for f in features}
    if k == 0:
        return base
    states = start["property_state"].to_numpy().astype(str)
    prev = add_months(start_ym, k - 1)  # inputs use the month before (as in the feature table)
    out = dict(base)
    out["loan_age"] = base["loan_age"] + k
    out["calendar_month"] = ((base["calendar_month"] - 1 + k) % 12 + 1).astype(float)
    balance = scheduled_balance(start["upb_start"].to_numpy(), start["rate_start"].to_numpy(),
                                start["remaining_start"].to_numpy(), k)  # fmt: skip
    value = (start["orig_upb"].to_numpy(dtype=float) / (start["orig_ltv"].to_numpy(dtype=float) / 100.0)
             * macro.by_state(macro.hpi, prev, states) / start["hpi_orig"].to_numpy(dtype=float))  # fmt: skip
    with np.errstate(divide="ignore", invalid="ignore"):
        out["current_ltv"] = 100.0 * balance / value
    ur = macro.by_state(macro.unemployment, prev, states)
    out["unemployment"] = ur
    out["unemployment_change_12"] = ur - macro.by_state(
        macro.unemployment, add_months(start_ym, k - 13), states
    )
    out["rate_incentive"] = start["rate_start"].to_numpy(dtype=float) - macro.national(prev, len(start))
    out["forbearance_prev"] = np.zeros(len(start))
    if "hpi_gain" in out:
        with np.errstate(divide="ignore", invalid="ignore"):
            out["hpi_gain"] = (
                macro.by_state(macro.hpi, prev, states) / start["hpi_orig"].to_numpy(dtype=float) - 1.0
            )
    return out


def last_month_state(state: int, slices: np.ndarray) -> np.ndarray:
    """The input "state last month" for probability in `state`, from its slice (see SLICES)."""
    if state == fx.CURRENT:
        return np.where(slices == 1, 1.0, np.where(slices == FROM_60, 2.0, 0.0))
    return slices.astype(float)


def start_slices(state0: np.ndarray, last0: np.ndarray) -> np.ndarray:
    """Each loan's slice in the start month, from its starting state and its actual state last month."""
    last0 = np.nan_to_num(last0, nan=0.0).astype(int).clip(0, 2)
    current = np.where(last0 == 1, 1, np.where(last0 == 2, FROM_60, CLEAN))
    return np.where(state0 == fx.CURRENT, current, last0)


def start_late_in_window(history: np.ndarray, k: int) -> np.ndarray:
    """The loan's late months before the start still inside projected month k's 12-month window.
    `history` is loans x 12 (column j-1 = the row j months before the start). Month k's window is the
    12 rows before it, so the start row j months back is in it while j <= 12 - k."""
    keep = max(12 - k, 0)
    return history[:, :keep].sum(axis=1) if keep else np.zeros(len(history))


def check_history(start: Any) -> int:
    """Loans whose 12 history flags do not add up to their start row's late_months_12 (should be 0)."""
    flags = start[list(HISTORY)].to_numpy(dtype=float).sum(axis=1)
    return int((flags != start["late_months_12"].to_numpy(dtype=float)).sum())


def late_input(start_late: np.ndarray, count: np.ndarray, k: int, state: int) -> np.ndarray:
    """The input "months late in the past 12" for projected month k, as a whole number: the loan's late
    months before the start still in the window (`start_late`, from start_late_in_window) plus the
    slice's projected late months, rounded, plus this month's own state (see the module note)."""
    base = np.floor(start_late + count + 0.5)
    if state == fx.LATE_30:
        base = base + 1.0
    elif state == fx.LATE_60:
        base = np.maximum(base, 1.0) + 1.0
    return np.clip(base, 0.0, 12.0)


@dataclass
class Projection:
    p_default: np.ndarray  # loans x months: chance of defaulting in that month
    p_prepay: np.ndarray  # loans x months: chance of prepaying in that month
    months: list[str]  # YYYYMM of each projected month


def _add(target: np.ndarray, rows: np.ndarray, state: int, slices: np.ndarray, values: np.ndarray) -> None:
    """target[rows, state, slices] += values, adding up repeated rows (a loan can send probability
    from several slices into the same one)."""
    flat = np.ravel_multi_index((rows, np.full(len(rows), state), slices), target.shape)
    target.reshape(-1)[:] += np.bincount(flat, values, minlength=target.size)


def project(models: dict[int, Any], start: Any, macro: Macro, start_ym: str, months: int) -> Projection:
    """Play every loan in `start` (the projection start table) forward `months` months with one
    transition model per live state (`models[state].predict_proba(inputs)` -> n x 5).

    Each loan's probability sits in slices (live state x months since its last projected late month);
    `count` holds, per slice, probability x projected late months before this month, so count / mass
    is the slice's late-month count (see the module note)."""
    n = len(start)
    state0 = start["state_prev"].to_numpy(dtype=int)
    mass = np.zeros((n, len(LIVE), SLICES))
    last0 = start["state_last_month"].to_numpy(dtype=float) if "state_last_month" in start else np.zeros(n)
    mass[np.arange(n), state0, start_slices(state0, last0)] = 1.0
    count = np.zeros_like(mass)
    p_def, p_pre = np.zeros((n, months)), np.zeros((n, months))
    history = start[list(HISTORY)].to_numpy(dtype=float)
    feature_sets = {s: tuple(getattr(models[s], "features", fx.TRANSITION_FEATURES)) for s in LIVE}
    every = tuple(dict.fromkeys(f for fs in feature_sets.values() for f in fs))
    for k in range(months):
        X_all = month_inputs(start, macro, start_ym, k, every)
        start_late = start_late_in_window(history, k)
        new_mass, new_count = np.zeros_like(mass), np.zeros_like(count)
        for s in LIVE:
            cells = [(j, np.flatnonzero(mass[:, s, j] > TINY)) for j in range(SLICES)]
            cells = [(j, r) for j, r in cells if len(r)]
            if not cells:
                continue
            rows = np.concatenate([r for _, r in cells])
            js = np.concatenate([np.full(len(r), j) for j, r in cells])
            m, c = mass[rows, s, js], count[rows, s, js]
            X = {f: X_all[f][rows] for f in feature_sets[s]}
            if k > 0 and "late_months_12" in X:
                X["late_months_12"] = late_input(start_late[rows], np.where(m > 0, c / m, 0.0), k, s)
            if "state_last_month" in X:
                X["state_last_month"] = last_month_state(s, js)
            P = models[s].predict_proba(X)
            moved, moved_count = m[:, None] * P, c[:, None] * P
            p_def[:, k] += np.bincount(rows, moved[:, fx.DEFAULTED], minlength=n)
            p_pre[:, k] += np.bincount(rows, moved[:, fx.PREPAID], minlength=n)
            # this month's state joins next month's history when it is a late state; in month 0 it
            # is the month before the start, already in the start history (not counted twice)
            joins = moved if (s != fx.CURRENT and k > 0) else np.zeros_like(moved)
            for d in (fx.LATE_30, fx.LATE_60):  # into the late state, slice = where it came from (s)
                _add(new_mass, rows, d, np.full(len(rows), s), moved[:, d])
                _add(new_count, rows, d, np.full(len(rows), s), moved_count[:, d] + joins[:, d])
            # back to (or staying) current: one month further from the last late month; after 12
            # months the projected late months have left the window
            if s == fx.CURRENT:
                j_next = np.where(js == FROM_60, 2, np.minimum(js + 1, CLEAN))
            else:
                j_next = np.full(len(rows), 1 if s == fx.LATE_30 else FROM_60)
            to_current = moved_count[:, fx.CURRENT] + joins[:, fx.CURRENT]
            _add(new_mass, rows, fx.CURRENT, j_next, moved[:, fx.CURRENT])
            _add(new_count, rows, fx.CURRENT, j_next, np.where(j_next == CLEAN, 0.0, to_current))
        mass, count = new_mass, new_count
    return Projection(p_def, p_pre, [add_months(start_ym, k) for k in range(months)])


# ---------------------------------------------------------------------------- the projection check


@dataclass(frozen=True)
class Cell:
    event: str  # default or prepaid
    by: str  # year or vintage
    group: str
    actual: float
    projected: float
    ratio: float
    applies: bool  # at least MIN_EVENTS_FOR_BAR actual events
    passes: bool


@dataclass
class Check:
    cells: list[Cell] = field(default_factory=list)
    loans: int = 0
    totals: dict[str, float] = field(default_factory=dict)
    # reported, not judged: by starting state, and month by month
    by_start_state: dict[str, dict[str, float]] = field(default_factory=dict)
    monthly: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return all(c.passes for c in self.cells)

    def failing(self) -> list[Cell]:
        return [c for c in self.cells if not c.passes]

    def as_dict(self) -> dict[str, Any]:
        return {"loans": self.loans, "totals": self.totals, "passes": self.passes,
                "cells": [asdict(c) for c in self.cells], "by_start_state": self.by_start_state,
                "monthly": self.monthly}  # fmt: skip


def observed_months(start_ym: str, actuals: Any) -> tuple[np.ndarray, np.ndarray]:
    """Per loan: months observed (a censored last month's outcome is unknown, so it is not counted)
    and the actual event code (DEFAULTED, PREPAID or -1) in its last observed month."""
    last = actuals["last_ym"].to_numpy().astype(str)
    status = actuals["last_status"].to_numpy().astype(str)
    m = np.array([months_between(start_ym, x) for x in last])
    observed = np.where(status == fx.EXCLUDED_STATUS, m - 1, m)
    event = np.where(status == "defaulted", fx.DEFAULTED, np.where(status == "prepaid", fx.PREPAID, -1))
    return observed, event


def check(projection: Projection, start: Any, actuals: Any, start_ym: str, cut_at_exit: bool = True) -> Check:
    """Projected vs actual defaults and prepayments by year and by vintage, against the ADR-009 bar
    (0.80-1.25 where a cell has at least 200 actual events).

    `cut_at_exit=True` is the rule every recorded check used: projected chances count only up to the month
    the loan actually left (paid off, defaulted or censored). That under-counts the projection (L-039): its
    chances already include the loan's own chance of leaving, so cutting at an actual payoff or default
    counts survival twice. `cut_at_exit=False` (the corrected count, for the record) cuts only where the
    outcome is unknown (censored), and counts every projected month for loans that paid off or defaulted."""
    a = actuals.set_index("loan_id").reindex(start["loan_id"]).reset_index()
    observed, event = observed_months(start_ym, a)
    months = len(projection.months)
    k = np.arange(months)[None, :]
    counted = (
        observed if cut_at_exit else np.where(np.isin(event, [fx.DEFAULTED, fx.PREPAID]), months, observed)
    )
    seen = k < counted[:, None]
    years = np.array([ym[:4] for ym in projection.months])
    vintage = start["vintage_year"].astype(str).to_numpy()
    last_index = observed - 1
    result = Check(loans=len(start))
    for name, code, proj in (("default", fx.DEFAULTED, projection.p_default), ("prepaid", fx.PREPAID, projection.p_prepay)):  # fmt: skip
        p = np.where(seen, proj, 0.0)
        happened = (event == code) & (last_index >= 0) & (last_index < months)
        event_year = np.where(happened, years[np.clip(last_index, 0, months - 1)], "")
        result.totals[f"{name}_actual"] = float(happened.sum())
        result.totals[f"{name}_projected"] = float(p.sum())
        for y in sorted(set(years.tolist())):
            result.cells.append(
                _cell(name, "year", y, float((event_year == y).sum()), float(p[:, years == y].sum()))
            )
        for vin in sorted(set(vintage.tolist())):
            m = vintage == vin
            result.cells.append(_cell(name, "vintage", vin, float(happened[m].sum()), float(p[m].sum())))
        state0 = start["state_prev"].to_numpy(dtype=int)
        for s in LIVE:
            m = state0 == s
            row = result.by_start_state.setdefault(fx.STATES[s], {"loans": float(m.sum())})
            row[f"{name}_actual"] = float(happened[m].sum())
            row[f"{name}_projected"] = float(p[m].sum())
        for i, ym in enumerate(projection.months):
            if len(result.monthly) <= i:
                result.monthly.append({"month": ym})
            result.monthly[i][f"{name}_actual"] = float((happened & (last_index == i)).sum())
            result.monthly[i][f"{name}_projected"] = float(p[:, i].sum())
    return result


def _cell(event: str, by: str, group: str, actual: float, projected: float) -> Cell:
    ratio = projected / actual if actual else float("inf")
    applies = actual >= metrics.MIN_EVENTS_FOR_BAR
    passes = (not applies) or metrics.CALIBRATION_LOW <= ratio <= metrics.CALIBRATION_HIGH
    return Cell(event, by, group, actual, projected, ratio, applies, passes)


def newly_failing(chosen: Check, benchmark: Check) -> list[Cell]:
    """Cells the chosen combination fails that the all-logistic combination passes (the combination
    check, ADR-009 Amendment 1)."""
    bench = {(c.event, c.by, c.group): c for c in benchmark.cells}
    return [
        c
        for c in chosen.failing()
        if bench.get((c.event, c.by, c.group)) and bench[(c.event, c.by, c.group)].passes
    ]
