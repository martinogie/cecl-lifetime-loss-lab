"""Unit tests for cecl.macro (monthly rules for the economic series). Synthetic numbers only."""

from __future__ import annotations

import math
from datetime import date

import pytest

from cecl import macro


def test_month_helpers() -> None:
    assert macro.add_months(date(2025, 11, 1), 3) == date(2026, 2, 1)
    assert macro.add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert macro.months_between(date(2025, 11, 1), date(2026, 2, 1)) == 3
    assert macro.quarter_mid_month(2026, 2) == date(2026, 5, 1)
    assert macro.quarter_end_month(2026, 2) == date(2026, 6, 1)
    with pytest.raises(ValueError):
        macro.quarter_mid_month(2026, 5)


def test_weekly_to_monthly_averages_and_drops_unfinished_month() -> None:
    obs = [
        (date(2026, 1, 1), 6.0),
        (date(2026, 1, 8), 7.0),
        (date(2026, 1, 15), None),  # a missing week is skipped, not counted as zero
        (date(2026, 2, 5), 5.0),
        (date(2026, 3, 5), 4.0),  # March is the latest month: unfinished, left out
    ]
    out = macro.weekly_to_monthly(obs)
    assert [(v.month, v.value, v.method) for v in out] == [
        (date(2026, 1, 1), 6.5, macro.WEEKLY_MEAN),
        (date(2026, 2, 1), 5.0, macro.WEEKLY_MEAN),
    ]


def test_quarterly_to_monthly_midpoints_and_constant_growth() -> None:
    q = {(2025, 4): 100.0, (2026, 1): 103.0, (2026, 2): 106.09}
    out = macro.quarterly_to_monthly(q)
    months = [v.month for v in out]
    assert months[0] == date(2025, 11, 1) and months[-1] == date(2026, 5, 1)
    assert len(out) == 7  # Nov..May
    by = {v.month: v for v in out}
    assert by[date(2026, 2, 1)].value == 103.0 and by[date(2026, 2, 1)].method == macro.REPORTED
    # constant monthly growth between midpoints: Dec and Jan sit on the log-straight line
    g = (103.0 / 100.0) ** (1 / 3)
    assert by[date(2025, 12, 1)].value == pytest.approx(100 * g)
    assert by[date(2026, 1, 1)].value == pytest.approx(100 * g * g)
    assert by[date(2026, 1, 1)].method == macro.INTERPOLATED
    assert not any(v.provisional for v in out)


def test_quarterly_months_average_back_to_the_quarter() -> None:
    q = {(2025, 3): 100.0, (2025, 4): 102.0, (2026, 1): 104.0, (2026, 2): 106.0}
    by = {v.month: v.value for v in macro.quarterly_to_monthly(q)}
    q4_months = [by[date(2025, 10, 1)], by[date(2025, 11, 1)], by[date(2025, 12, 1)]]
    assert sum(q4_months) / 3 == pytest.approx(102.0, rel=1e-4)


def test_quarterly_extension_is_provisional_and_capped() -> None:
    q = {(2026, 1): 100.0, (2026, 2): 103.0}
    out = macro.quarterly_to_monthly(q, extend_through=date(2026, 6, 30))
    last = out[-1]
    assert last.month == date(2026, 6, 1)
    assert last.method == macro.EXTRAPOLATED and last.provisional
    assert last.value == pytest.approx(103.0 * math.exp(math.log(1.03) / 3))
    with pytest.raises(ValueError, match="stale"):
        macro.quarterly_to_monthly(q, extend_through=date(2026, 9, 1))  # 4 months > 2


def test_quarterly_rejects_gaps_bad_values_and_too_little_data() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        macro.quarterly_to_monthly({(2025, 1): 1.0, (2025, 3): 1.0})
    with pytest.raises(ValueError, match="positive"):
        macro.quarterly_to_monthly({(2025, 1): 1.0, (2025, 2): 0.0})
    with pytest.raises(ValueError, match="two quarters"):
        macro.quarterly_to_monthly({(2025, 1): 1.0})


def test_fill_known_gaps_straight_line_and_marked() -> None:
    rows = [(date(2025, 9, 1), 4.4), (date(2025, 10, 1), None), (date(2025, 11, 1), 4.6)]
    out = macro.fill_known_gaps(rows, [date(2025, 10, 1)])
    assert out[1].value == pytest.approx(4.5) and out[1].method == macro.GAP_FILLED
    assert out[0].method == out[2].method == macro.REPORTED


def test_fill_known_gaps_refuses_unexpected_or_unbounded_gaps() -> None:
    rows = [(date(2025, 9, 1), 4.4), (date(2025, 10, 1), None), (date(2025, 11, 1), 4.6)]
    with pytest.raises(ValueError, match="not a known gap"):
        macro.fill_known_gaps(rows, [])
    with pytest.raises(ValueError, match="both sides"):
        macro.fill_known_gaps(rows[:2], [date(2025, 10, 1)])
    with pytest.raises(ValueError, match="consecutive"):
        macro.fill_known_gaps([rows[0], rows[2]], [])


def test_long_run_average_and_growth() -> None:
    vals = [macro.MonthValue(macro.add_months(date(2000, 1, 1), i), 100 * 1.01**i, "r") for i in range(25)]
    mean, n = macro.long_run_average(vals[:3], date(2000, 1, 1), date(2000, 3, 1))
    assert n == 3 and mean == pytest.approx((100 + 101 + 102.01) / 3)
    growth, n = macro.long_run_growth(vals, date(2000, 1, 1), date(2002, 1, 1))
    assert n == 25 and growth == pytest.approx(12 * math.log(1.01))
    with pytest.raises(ValueError):
        macro.long_run_average(vals, date(1990, 1, 1), date(1990, 2, 1))
    with pytest.raises(ValueError):
        macro.long_run_growth(vals[:5], date(2000, 1, 1), date(2002, 1, 1))


def _fed(level: float) -> dict[tuple[int, int], dict[str, float]]:
    """Two quarters: 2026 Q2 and Q3, every variable moving."""
    base = {col: level for col in macro.FED_VARIABLES}
    nxt = {col: level + 1 for col in macro.FED_VARIABLES}
    nxt["House Price Index (Level)"] = level * 0.9
    return {(2026, 2): base, (2026, 3): nxt}


ANCHORS = {
    "unemployment_rate": 4.0,
    "tbill_3m": 3.0,
    "treasury_5y": 3.5,
    "treasury_10y": 4.0,
    "mortgage_rate_30y": 6.0,
    "hpi": 700.0,
}
START = {col: 5.0 for col in macro.FED_VARIABLES}  # the Fed's starting point (its 2025 Q4)
TWO = {"baseline": _fed(10.0), "severely_adverse": _fed(20.0)}


def test_quarter_helpers() -> None:
    assert macro.add_quarters((2025, 4), 1) == (2026, 1)
    assert macro.add_quarters((2026, 1), -1) == (2025, 4)
    assert macro.quarter_of(date(2026, 6, 1)) == (2026, 2)


def test_fed_paths_shifted_uses_every_fed_quarter_from_the_start() -> None:
    out = macro.fed_paths(TWO, ANCHORS, START, (2026, 3))
    by = {(p.scenario, p.variable, p.horizon): p for p in out}
    assert {p.horizon for p in out} == {1, 2} and len(out) == 3 * 2 * len(macro.FED_VARIABLES)
    first = by[("severely_adverse", "unemployment_rate", 1)]
    # Fed 2026 Q2 lands on our 2026 Q3, measured from the Fed's start: 4.0 + (20 - 5)
    assert (first.year, first.quarter, first.fed_year, first.fed_quarter) == (2026, 3, 2026, 2)
    assert first.basis == "points" and first.fed_base == 5.0 and first.path_value == 19.0
    second = by[("baseline", "hpi", 2)]
    assert (second.year, second.quarter) == (2026, 4)
    assert second.basis == "ratio" and second.path_value == pytest.approx(700.0 * 9.0 / 5.0)
    adverse = by[("adverse", "unemployment_rate", 2)]
    assert adverse.fed_value == 16.0  # halfway between 11 and 21


def test_fed_paths_calendar_timing_for_the_sensitivity_table() -> None:
    out = macro.fed_paths(TWO, ANCHORS, {}, (2026, 3), timing=macro.CALENDAR)
    by = {(p.scenario, p.variable): p for p in out}
    assert {p.horizon for p in out} == {1}  # only quarters from 2026 Q3, on their own dates
    ur = by[("severely_adverse", "unemployment_rate")]
    assert ur.fed_base == 20.0 and ur.fed_change == 1.0 and ur.path_value == 5.0
    hpi = by[("baseline", "hpi")]
    assert hpi.fed_change == pytest.approx(0.9) and hpi.path_value == pytest.approx(630.0)


def test_path_extremes() -> None:
    out = macro.fed_paths(TWO, ANCHORS, START, (2026, 3))
    worst = macro.path_extremes(out, "severely_adverse")
    assert worst["peak_unemployment"] == 20.0 and worst["peak_unemployment_quarter"] == "2026 Q4"
    assert worst["worst_hpi_change"] == pytest.approx(18.0 / 5.0 - 1)
    assert worst["worst_hpi_quarter"] == "2026 Q4"


def test_fed_paths_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="missing Fed scenario"):
        macro.fed_paths({"baseline": _fed(10.0)}, ANCHORS, START)
    with pytest.raises(ValueError, match="same quarters"):
        macro.fed_paths(
            {"baseline": _fed(10.0), "severely_adverse": {(2026, 2): _fed(1)[(2026, 2)]}}, ANCHORS, START
        )
    with pytest.raises(ValueError, match="timing"):
        macro.fed_paths(TWO, ANCHORS, START, timing="whenever")
    with pytest.raises(ValueError, match="no anchor"):
        macro.fed_paths(TWO, {"hpi": 1.0}, START)
    with pytest.raises(ValueError, match="starting value"):
        macro.fed_paths(TWO, ANCHORS, {"Unemployment rate": 4.5})
    with pytest.raises(ValueError, match="quarter before"):
        macro.fed_paths(TWO, ANCHORS, {}, (2026, 2), timing=macro.CALENDAR)
