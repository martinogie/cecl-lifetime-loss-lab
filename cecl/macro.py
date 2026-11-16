"""Economic series: the monthly rules (Phase 1 step 7, docs/phase1/step7_economic_data.md).

Pure functions with no downloads or files, so they are easy to test and run the same way on the PC,
in Airflow and on Databricks. `ingestion/macro_build.py` reads bronze, calls these, writes silver.

- `weekly_to_monthly`: average of the weeks in each month; a month is used only once it has ended.
- `quarterly_to_monthly`: rule M1/L1. A quarterly index (an average over the quarter) sits at the
  quarter's middle month; months between are filled with constant growth (a straight line in
  logs); after the last middle month, the last growth rate is carried forward for at most a few
  months, flagged provisional.
- `fill_known_gaps`: a month the source never published (October 2025 unemployment, L-021) is
  filled on a straight line between its neighbours and marked, never passed off as real.
- `long_run_average` / `long_run_growth`: ADR-003 D2 (1990 onward).
- `fed_paths`: ADR-003 timing (as amended 15 November 2026) and ADR-005 A1. The adverse path is
  halfway between baseline and severely adverse. The Fed's path is shifted to the reserve date: its
  first scenario quarter (2026 Q1) becomes 2026 Q3, and its changes, measured from the Fed's own
  starting point (2025 Q4), are added to the real June 2026 values.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

# Method labels written to silver, so every monthly value says how it was obtained.
REPORTED = "reported"
WEEKLY_MEAN = "weekly_mean"
INTERPOLATED = "interpolated"  # between two quarter midpoints (rule M1)
EXTRAPOLATED = "extrapolated"  # after the last quarter midpoint (rule L1); always provisional
GAP_FILLED = "gap_filled"  # a month the source never published (finding G, L-021)

MAX_EXTRAPOLATED_MONTHS = 2  # rule L1: more than this and the series is stale

# Fed scenario columns used by the models, and our names for them. "points" variables (rates,
# unemployment) carry over as point changes; "ratio" variables (index levels) as percentage changes,
# because the Fed's house price index is a different index from FHFA's.
FED_VARIABLES: dict[str, tuple[str, str]] = {
    "Unemployment rate": ("unemployment_rate", "points"),
    "3-month Treasury rate": ("tbill_3m", "points"),
    "5-year Treasury yield": ("treasury_5y", "points"),
    "10-year Treasury yield": ("treasury_10y", "points"),
    "Mortgage rate": ("mortgage_rate_30y", "points"),
    "House Price Index (Level)": ("hpi", "ratio"),
}
BASELINE = "baseline"
ADVERSE = "adverse"
SEVERELY_ADVERSE = "severely_adverse"

# How the Fed's quarters line up with ours (ADR-003, amended 15 November 2026).
SHIFTED = "shifted"  # the decision: Fed quarter 1 = the first quarter after the reserve date
CALENDAR = "calendar"  # ADR-003 as first written; kept only for the Phase 5 sensitivity table


@dataclass(frozen=True)
class MonthValue:
    month: date  # first day of the month
    value: float
    method: str
    provisional: bool = False


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    index = d.year * 12 + (d.month - 1) + n
    return date(index // 12, index % 12 + 1, 1)


def months_between(a: date, b: date) -> int:
    """Whole months from a to b (both read as their month)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def quarter_mid_month(year: int, quarter: int) -> date:
    """February, May, August or November: where a quarterly average belongs."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter}")
    return date(year, 3 * quarter - 1, 1)


def quarter_end_month(year: int, quarter: int) -> date:
    return date(year, 3 * quarter, 1)


def add_quarters(yq: tuple[int, int], n: int) -> tuple[int, int]:
    index = yq[0] * 4 + yq[1] - 1 + n
    return index // 4, index % 4 + 1


def quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def weekly_to_monthly(observations: Iterable[tuple[date, float | None]]) -> list[MonthValue]:
    """Average of the weekly values dated in each month.

    A month is kept only if a later observation exists in a later month: the current, unfinished
    month is left out, so its average never changes after it is first written.
    """
    by_month: dict[date, list[float]] = {}
    last_month: date | None = None
    for day, value in observations:
        m = month_start(day)
        last_month = m if last_month is None or m > last_month else last_month
        if value is not None:
            by_month.setdefault(m, []).append(value)
    return [
        MonthValue(m, sum(v) / len(v), WEEKLY_MEAN)
        for m, v in sorted(by_month.items())
        if last_month is not None and m < last_month
    ]


def quarterly_to_monthly(
    quarters: Mapping[tuple[int, int], float],
    extend_through: date | None = None,
    max_extrapolated: int = MAX_EXTRAPOLATED_MONTHS,
) -> list[MonthValue]:
    """Rule M1/L1: a quarterly index as monthly values.

    `quarters` maps (year, quarter) to a positive index value and must have no missing quarter.
    The series starts at the first quarter's middle month. Months between two middle months grow at
    a constant rate. If `extend_through` is later than the last middle month (e.g. the reserve
    month June, after May), the last quarter-on-quarter growth is carried forward, at most
    `max_extrapolated` months, each flagged provisional; asking for more raises ValueError.
    """
    if len(quarters) < 2:
        raise ValueError("need at least two quarters to fill months")
    keys = sorted(quarters)
    for (y0, q0), (y1, q1) in zip(keys, keys[1:], strict=False):
        if (y1 * 4 + q1) - (y0 * 4 + q0) != 1:
            raise ValueError(f"quarters must be contiguous: {y0}Q{q0} is followed by {y1}Q{q1}")
    for key in keys:
        if not quarters[key] > 0:
            raise ValueError(f"index values must be positive: {key[0]}Q{key[1]} = {quarters[key]}")

    out: list[MonthValue] = []
    for (ya, qa), (yb, qb) in zip(keys, keys[1:], strict=False):
        start, va, vb = quarter_mid_month(ya, qa), quarters[(ya, qa)], quarters[(yb, qb)]
        step = math.log(vb / va) / 3  # constant growth over the 3 months between midpoints
        out.append(MonthValue(start, va, REPORTED))
        out.extend(MonthValue(add_months(start, k), va * math.exp(step * k), INTERPOLATED) for k in (1, 2))
    last_key, prev_key = keys[-1], keys[-2]
    last_mid, last_value = quarter_mid_month(*last_key), quarters[last_key]
    out.append(MonthValue(last_mid, last_value, REPORTED))

    if extend_through is not None:
        extra = months_between(last_mid, month_start(extend_through))
        if extra > max_extrapolated:
            raise ValueError(
                f"{extra} months after the last quarter's middle month ({last_mid:%Y-%m}) would have to be "
                f"extrapolated; at most {max_extrapolated} are allowed (the series is stale)"
            )
        step = math.log(last_value / quarters[prev_key]) / 3
        out.extend(
            MonthValue(add_months(last_mid, k), last_value * math.exp(step * k), EXTRAPOLATED, True)
            for k in range(1, extra + 1)
        )
    return out


def fill_known_gaps(
    monthly: Sequence[tuple[date, float | None]], known_gaps: Iterable[date]
) -> list[MonthValue]:
    """Monthly values with each known gap filled on a straight line between its neighbours.

    Every month must be present (a missing value is passed as None). A None in a month that is not
    a known gap, or a known gap without a value on both sides, raises ValueError: an unexpected
    gap must stop the load, not be papered over.
    """
    gaps = {month_start(g) for g in known_gaps}
    rows = [(month_start(m), v) for m, v in monthly]
    for (a, _), (b, _) in zip(rows, rows[1:], strict=False):
        if months_between(a, b) != 1:
            raise ValueError(f"months must be consecutive: {a:%Y-%m} is followed by {b:%Y-%m}")
    out: list[MonthValue] = []
    for i, (m, v) in enumerate(rows):
        if v is not None:
            out.append(MonthValue(m, v, REPORTED))
            continue
        if m not in gaps:
            raise ValueError(f"{m:%Y-%m} is missing and is not a known gap")
        before = next((rows[j] for j in range(i - 1, -1, -1) if rows[j][1] is not None), None)
        after = next((rows[j] for j in range(i + 1, len(rows)) if rows[j][1] is not None), None)
        if before is None or after is None or before[1] is None or after[1] is None:
            raise ValueError(f"known gap {m:%Y-%m} has no reported value on both sides")
        share = months_between(before[0], m) / months_between(before[0], after[0])
        out.append(MonthValue(m, before[1] + share * (after[1] - before[1]), GAP_FILLED))
    return out


def window(values: Iterable[MonthValue], start: date, end: date) -> list[MonthValue]:
    return [v for v in values if start <= v.month <= end]


def long_run_average(values: Iterable[MonthValue], start: date, end: date) -> tuple[float, int]:
    """ADR-003 D2: mean level over [start, end] by month, and the number of months used."""
    chosen = [v.value for v in window(values, start, end)]
    if not chosen:
        raise ValueError(f"no values between {start:%Y-%m} and {end:%Y-%m}")
    return sum(chosen) / len(chosen), len(chosen)


def long_run_growth(values: Iterable[MonthValue], start: date, end: date) -> tuple[float, int]:
    """Average annual growth (log terms, e.g. 0.04 = about 4% a year) of an index over [start, end].

    House prices revert to their long-run growth, not to an average level (ADR-003 C1, D2).
    """
    chosen = window(values, start, end)
    if len(chosen) < 13:
        raise ValueError("need at least 13 months to measure annual growth")
    first, last = chosen[0], chosen[-1]
    years = months_between(first.month, last.month) / 12
    return math.log(last.value / first.value) / years, len(chosen)


@dataclass(frozen=True)
class FedPathPoint:
    scenario: str
    year: int  # our calendar quarter (2026 Q3 is the first forecast quarter)
    quarter: int
    horizon: int  # 1 = the first forecast quarter
    fed_year: int  # the Fed's own quarter whose change lands here
    fed_quarter: int
    variable: str
    fed_value: float
    fed_base: float  # the Fed value the change is measured from
    fed_change: float  # points since fed_base, or ratio to it (see `basis`)
    basis: str  # "points" or "ratio"
    anchor_value: float  # our real value in the anchor month (June 2026)
    path_value: float


def fed_paths(
    fed: Mapping[str, Mapping[tuple[int, int], Mapping[str, float]]],
    anchors: Mapping[str, float],
    start: Mapping[str, float],
    first_quarter: tuple[int, int] = (2026, 3),
    timing: str = SHIFTED,
) -> list[FedPathPoint]:
    """Baseline, adverse and severely adverse paths, anchored on our real values.

    `fed[scenario][(year, quarter)][fed column]` holds the Fed's numbers for "baseline" and
    "severely_adverse". `start[fed column]` is the Fed's starting point: its actual value for the
    quarter before the first scenario quarter (2025 Q4, from the Fed's history file).
    `anchors[variable]` is our real value for the anchor month (June 2026), by our variable names.
    The adverse path is built first, halfway between the two Fed paths quarter by quarter (ADR-005
    A1). Then each path value is
        points: anchor + (fed value - fed base)
        ratio:  anchor * (fed value / fed base)
    `timing` picks which Fed quarters are used and where they land (ADR-003 amendment):
        shifted:  every Fed quarter, from `start`; Fed quarter k lands on first_quarter + (k - 1).
                  Why: the Fed's recession starts in its first quarter; the real economy before
                  our reserve date didn't follow it, so measuring from later would lose that part.
        calendar: only Fed quarters from `first_quarter` on, on their own dates, measured from the
                  Fed's value in the quarter before (2026 Q2). Kept for the sensitivity table.
    """
    for name in (BASELINE, SEVERELY_ADVERSE):
        if name not in fed:
            raise ValueError(f"missing Fed scenario {name!r}")
    if set(fed[BASELINE]) != set(fed[SEVERELY_ADVERSE]):
        raise ValueError("baseline and severely adverse must cover the same quarters")
    if timing not in (SHIFTED, CALENDAR):
        raise ValueError(f"timing must be {SHIFTED!r} or {CALENDAR!r}, got {timing!r}")
    missing = [ours for ours, _ in FED_VARIABLES.values() if ours not in anchors]
    if missing:
        raise ValueError(f"no anchor value for {', '.join(missing)}")
    if timing == SHIFTED:
        gaps = [col for col in FED_VARIABLES if col not in start]
        if gaps:
            raise ValueError(f"no Fed starting value for {', '.join(gaps)}")
    elif add_quarters(first_quarter, -1) not in fed[BASELINE]:
        raise ValueError(f"the quarter before {first_quarter} is not in the Fed paths")

    scenarios: dict[str, Mapping[tuple[int, int], Mapping[str, float]]] = {
        BASELINE: fed[BASELINE],
        ADVERSE: {
            q: {col: (fed[BASELINE][q][col] + fed[SEVERELY_ADVERSE][q][col]) / 2 for col in FED_VARIABLES}
            for q in fed[BASELINE]
        },
        SEVERELY_ADVERSE: fed[SEVERELY_ADVERSE],
    }
    out: list[FedPathPoint] = []
    for scenario, path in scenarios.items():
        if timing == SHIFTED:
            base, used = start, sorted(path)  # same starting point for all three scenarios
        else:
            base = path[add_quarters(first_quarter, -1)]
            used = sorted(q for q in path if q >= first_quarter)
        for horizon, fed_q in enumerate(used, start=1):
            year, quarter = add_quarters(first_quarter, horizon - 1) if timing == SHIFTED else fed_q
            for col, (ours, basis) in FED_VARIABLES.items():
                value, anchor = path[fed_q][col], anchors[ours]
                if basis == "points":
                    change = value - base[col]
                    result = anchor + change
                else:
                    change = value / base[col]
                    result = anchor * change
                out.append(
                    FedPathPoint(
                        scenario, year, quarter, horizon, fed_q[0], fed_q[1], ours,
                        value, base[col], change, basis, anchor, result,
                    )
                )  # fmt: skip
    return out


def path_extremes(points: Iterable[FedPathPoint], scenario: str) -> dict[str, object]:
    """The worst point of a path, for the run log: peak unemployment and the deepest house price fall."""
    chosen = [p for p in points if p.scenario == scenario]
    ur = max((p for p in chosen if p.variable == "unemployment_rate"), key=lambda p: p.path_value)
    hpi = min((p for p in chosen if p.variable == "hpi"), key=lambda p: p.path_value)
    return {
        "peak_unemployment": round(ur.path_value, 2),
        "peak_unemployment_quarter": f"{ur.year} Q{ur.quarter}",
        "worst_hpi_change": round(hpi.path_value / hpi.anchor_value - 1, 4),
        "worst_hpi_quarter": f"{hpi.year} Q{hpi.quarter}",
    }
