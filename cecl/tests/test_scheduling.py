from __future__ import annotations

from datetime import UTC, datetime

import pytest

import cecl
from cecl import scheduling as s


def test_package_has_version():
    assert cecl.__version__


@pytest.mark.parametrize(
    "cron",
    [
        None,
        "0 7 * * 3",  # Wednesday 07:00: the health check
        "0 7 * * wed",
        "0 13 * * 1",  # Monday 13:00 is allowed
        "30 14 * * mon",
        "0 7 * * 2-5",
        "0 4 * * 0,6",  # 04:00 is just after the reboot window
        "29 2 * * 3",  # 02:29 is just before it
        "0 7 * * */2",  # Sun, Tue, Thu, Sat
        "0 7 * * 7",  # 7 = Sunday
        "0 13-23 * * *",
        "@weekly",  # Sunday 00:00
    ],
)
def test_allowed_schedules(cron):
    assert s.schedule_problems(cron) == []


@pytest.mark.parametrize(
    "cron",
    [
        "0 6 * * 1",
        "0 12 * * mon",
        "59 12 * * 1",
        "0 7 * * *",
        "0 4 * * *",
        "0 7 15 * *",
        "0 7 * * MON-FRI",
        "0 7 * * 1/7",
        "@daily",
        "@monthly",
    ],
)
def test_monday_morning_schedules_fail(cron):
    problems = s.schedule_problems(cron)
    assert any("Monday before 13:00" in p for p in problems)


@pytest.mark.parametrize("cron", ["30 2 * * 3", "0 3 * * 2", "59 3 * * 6", "*/15 * * * 3", "0 2-4 * * 5"])
def test_reboot_window_schedules_fail(cron):
    problems = s.schedule_problems(cron)
    assert problems == [f"{cron!r} can fire between 02:30 and 04:00 Chicago (VM reboot window)"]


def test_hourly_breaks_both_rules():
    assert len(s.schedule_problems("@hourly")) == 2


@pytest.mark.parametrize("cron", ["0 7 * *", "0 25 * * 3", "61 7 * * 3", "0 7 * * 8", "*/0 7 * * 3"])
def test_bad_cron_is_rejected(cron):
    with pytest.raises(ValueError):
        s.schedule_problems(cron)


# November 2026 is Central Standard Time: Chicago = UTC - 6 hours.
@pytest.mark.parametrize(
    ("utc", "reason"),
    [
        (datetime(2026, 11, 9, 6, 0, tzinfo=UTC), "Bank Health"),  # Mon 00:00
        (datetime(2026, 11, 9, 12, 0, tzinfo=UTC), "Bank Health"),  # Mon 06:00
        (datetime(2026, 11, 9, 18, 59, tzinfo=UTC), "Bank Health"),  # Mon 12:59
        (datetime(2026, 11, 9, 19, 0, tzinfo=UTC), None),  # Mon 13:00
        (datetime(2026, 11, 9, 5, 59, tzinfo=UTC), None),  # Sun 23:59
        (datetime(2026, 11, 10, 8, 29, tzinfo=UTC), None),  # Tue 02:29
        (datetime(2026, 11, 10, 8, 30, tzinfo=UTC), "reboot"),  # Tue 02:30
        (datetime(2026, 11, 10, 9, 59, tzinfo=UTC), "reboot"),  # Tue 03:59
        (datetime(2026, 11, 10, 10, 0, tzinfo=UTC), None),  # Tue 04:00
        (datetime(2026, 11, 11, 13, 0, tzinfo=UTC), None),  # Wed 07:00
    ],
)
def test_blocked_windows(utc, reason):
    got = s.blocked_reason(utc)
    if reason is None:
        assert got is None
    else:
        assert got is not None and reason in got


def test_guard_raises_in_a_blocked_window():
    with pytest.raises(s.BlockedWindow, match="Mon 2026-11-09 06:00 America/Chicago"):
        s.refuse_blocked_window(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    s.refuse_blocked_window(datetime(2026, 11, 11, 13, 0, tzinfo=UTC))


def test_guard_uses_now_by_default():
    # Only checks that the default path runs; the result depends on the current time.
    try:
        s.refuse_blocked_window()
    except s.BlockedWindow:
        pass
