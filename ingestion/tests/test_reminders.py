"""The quarterly release reminder (ingestion/reminders.py)."""

from __future__ import annotations

from datetime import date

from cecl import scheduling
from ingestion import reminders


def test_quarter_to_close_is_two_quarter_ends_back() -> None:
    assert reminders.quarter_to_close(date(2027, 1, 15)) == "202609"
    assert reminders.quarter_to_close(date(2027, 4, 15)) == "202612"
    assert reminders.quarter_to_close(date(2027, 7, 15)) == "202703"
    assert reminders.quarter_to_close(date(2027, 10, 15)) == "202706"


def test_text_names_the_quarter_and_the_steps() -> None:
    t = reminders.text(date(2027, 1, 15))
    assert "reserve_ym=202609" in t and "cecl_quarterly_close" in t and "browser" in t
    assert "not a Monday before 13:00" in t


def test_schedule_is_clear_of_the_blocked_windows() -> None:
    assert scheduling.schedule_problems("0 14 15 1,4,7,10 *") == []
