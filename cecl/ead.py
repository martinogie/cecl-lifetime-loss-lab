"""EAD: exposure at default, the balance still owed in each future month (Phase 3; roadmap 5.3).

For a fixed-rate mortgage the balance is mostly mechanical: the payment schedule from the rate and the
remaining term (`scheduled_balance`). Two things move it off the schedule:
- **partial prepayment** (curtailment): borrowers who pay a little extra principal; estimated here as a
  monthly rate by loan age from the training years and applied on top of the schedule;
- **full prepayment and default**: handled by the transition models (the loan leaves the book), not here.
Where the schedule does not hold (modified loans, deferred non-interest-bearing balances), the EAD
check below excludes them and says so; the reserve uses each loan's reported balance at the reserve
date, so a deferral already in the balance is carried.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# One payment schedule for the whole project: the multi-state projection already uses it.
from cecl.models.multistate import scheduled_balance

AGE_BANDS = ((0, 12), (12, 36), (36, 60), (60, 120), (120, 999))  # months
TOLERANCE = 0.02  # projected / actual balance (balance-weighted) must sit within 2% on the held-out loans


def age_band(age_months: Any) -> np.ndarray:
    age = np.asarray(age_months, dtype=float)
    out = np.full(len(age), len(AGE_BANDS) - 1)
    for j, (lo, hi) in reversed(list(enumerate(AGE_BANDS))):
        out[(age >= lo) & (age < hi)] = j
    return out


def project(
    upb: Any, rate_pct: Any, remaining: Any, age: Any, k: int, curtail: dict[int, float]
) -> np.ndarray:
    """Balance after k months for a loan still alive: the schedule, less partial prepayment at the
    monthly rate of each month's age band (compounded)."""
    sched = scheduled_balance(np.asarray(upb), np.asarray(rate_pct), np.asarray(remaining), k)
    return sched * keep_factor(age, k, curtail)


def keep_factor(age: Any, k: int, curtail: dict[int, float]) -> np.ndarray:
    """The share of the balance not paid off early over months age .. age + k - 1: the product of
    (1 - monthly rate of the month's age band). Computed per band from the months spent in it (closed
    form), the same as multiplying month by month but fast enough for a whole book over 30 years."""
    age = np.asarray(age, dtype=float)
    log_keep = np.zeros(len(age))
    for j, (lo, hi) in enumerate(AGE_BANDS):
        rate = curtail.get(j, 0.0)
        if rate <= 0.0:
            continue
        months = np.clip(np.minimum(age + k, hi) - np.maximum(age, lo), 0.0, None)  # months in this band
        log_keep += months * np.log1p(-rate)
    return np.exp(log_keep)


def curtailment_sql(perf: str, first: str, last: str) -> str:
    """Monthly partial-prepayment rate by loan-age band: for loan-months current this month and last,
    not modified, not paid off, extra principal = scheduled balance after one payment - actual balance,
    as a share of last month's balance. Months with a balance of 0 are skipped."""
    bands = " ".join(
        f"when p.loan_age >= {lo} and p.loan_age < {hi} then {j}" for j, (lo, hi) in enumerate(AGE_BANDS)
    )
    return f"""
with p as (
    select loan_id, period, loan_age, cast(current_upb as double) as upb, cast(current_interest_rate as double) as rate,
           remaining_months, delinquency_status, modification_flag, zero_balance_code,
           lag(cast(current_upb as double)) over w as upb_prev, lag(remaining_months) over w as rem_prev,
           lag(delinquency_status) over w as dq_prev
    from {perf}
    window w as (partition by loan_id order by period)
),
m as (
    select case {bands} else {len(AGE_BANDS) - 1} end as band, upb_prev, upb,
           upb_prev * (power(1 + rate / 1200, rem_prev) - power(1 + rate / 1200, 1))
               / (power(1 + rate / 1200, rem_prev) - 1) as sched
    from p
    where period between '{first}' and '{last}' and delinquency_status = '00' and dq_prev = '00'
      and coalesce(modification_flag, 'N') not in ('Y', 'P') and zero_balance_code is null
      and upb_prev > 0 and upb > 0 and rem_prev > 1 and rate > 0
)
select band, count(*) as months,
       sum(greatest(least(sched - upb, upb_prev), 0)) / sum(upb_prev) as rate
from m group by band order by band
"""


def check_sql(perf: str, start: str, horizons: tuple[int, ...] = (12, 24, 36)) -> str:
    """Held-out check: loans current, unmodified and alive at `start`, with their balance, rate, remaining
    term and age then, and their actual balance `h` months later if still current and unmodified."""
    cols = ", ".join(
        f"max(case when f.period = strftime(strptime('{start}', '%Y%m') + interval {h} month, '%Y%m') "
        f"and f.delinquency_status = '00' and coalesce(f.modification_flag, 'N') not in ('Y', 'P') and f.zero_balance_code is null "
        f"then cast(f.current_upb as double) end) as upb_{h}"
        for h in horizons
    )
    return f"""
with s as (
    select loan_id, cast(current_upb as double) as upb, cast(current_interest_rate as double) as rate,
           remaining_months as remaining, loan_age as age
    from {perf}
    where period = '{start}' and delinquency_status = '00' and coalesce(modification_flag, 'N') not in ('Y', 'P')
      and zero_balance_code is null and current_upb > 0 and remaining_months > 0
)
select s.*, {cols}
from s join {perf} f on f.loan_id = s.loan_id
group by s.loan_id, s.upb, s.rate, s.remaining, s.age
"""


def check(
    frame: Any, curtail: dict[int, float], horizons: tuple[int, ...] = (12, 24, 36)
) -> list[dict[str, float]]:
    """Projected vs actual balance h months ahead for loans still current then (balance-weighted ratio,
    schedule only and with partial prepayment; median absolute error per loan)."""
    rows = []
    for h in horizons:
        f = frame[frame[f"upb_{h}"].notna() & (frame[f"upb_{h}"] > 0)]
        actual = f[f"upb_{h}"].to_numpy(dtype=float)
        sched = scheduled_balance(f["upb"], f["rate"], f["remaining"], h)
        proj = project(f["upb"], f["rate"], f["remaining"], f["age"], h, curtail)
        rows.append({"months_ahead": h, "loans": int(len(f)), "schedule_only": float(sched.sum() / actual.sum()),
                     "with_partial_prepayment": float(proj.sum() / actual.sum()),
                     "median_abs_error": float(np.median(np.abs(proj / actual - 1.0))),
                     "within_tolerance": bool(abs(proj.sum() / actual.sum() - 1.0) <= TOLERANCE)})  # fmt: skip
    return rows
