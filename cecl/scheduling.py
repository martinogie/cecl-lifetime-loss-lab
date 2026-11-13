"""When CECL may run: the shared-VM rules, as code.

Prod shares an Oracle Cloud VM and a Databricks workspace with Bank Health. Two windows are
blocked, in America/Chicago time (Airflow's default timezone in every CECL stack):

  - Monday 00:00 to 12:59. Bank Health's weekly prod run starts at 06:00 and must finish by noon;
    13:00 leaves an hour of margin.
  - Every day 02:30 to 03:59. Unattended upgrades may reboot the VM at 03:00.

`schedule_problems(cron)` is used by the DAG import check in CI: a DAG whose schedule can fire in a
blocked window fails CI. `refuse_blocked_window()` is the first thing every task runs, so a manual
trigger in a blocked window also stops.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
MONDAY = 0  # datetime.weekday()
CRON_MONDAY = 1  # cron day-of-week numbering (0 or 7 = Sunday)
MONDAY_BLOCK_ENDS_HOUR = 13  # Monday runs may start at 13:00 Chicago at the earliest
REBOOT_WINDOW = ((2, 30), (4, 0))  # [02:30, 04:00) Chicago, every day

CRON_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@quarterly": "0 0 1 */3 *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


class BlockedWindow(RuntimeError):
    """A run tried to start in a window reserved for Bank Health or the VM's reboot."""


def _number(text: str) -> int:
    return DAY_NAMES[text[:3]] if text[:3] in DAY_NAMES else int(text)


def _expand(field: str, lo: int, hi: int) -> set[int]:
    """Values named by one cron field: *, 5, 1-5, mon-fri, */15, 5/15, and lists of these."""
    values: set[int] = set()
    for part in field.lower().split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part in ("*", "?"):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _number(a), _number(b)
        else:
            start = _number(part)
            end = hi if step > 1 else start  # "5/15" means from 5, every 15
        if start < lo or end > hi or step < 1:
            raise ValueError(f"cron field {field!r} is out of range {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return values


def _in_reboot_window(hour: int, minute: int) -> bool:
    (start_h, start_m), (end_h, end_m) = REBOOT_WINDOW
    return (start_h, start_m) <= (hour, minute) < (end_h, end_m)


def schedule_problems(schedule: str | None) -> list[str]:
    """Why a cron schedule breaks the shared-VM rules ([] if it doesn't). None = manual only.

    A schedule restricted by day-of-month (e.g. '0 7 15 * *') can land on any weekday, so it is
    treated as able to fire on a Monday: pick weekdays explicitly instead.
    """
    if schedule is None:
        return []
    cron = CRON_ALIASES.get(schedule.strip(), schedule.strip())
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"not a 5-field cron expression: {schedule!r}")
    minute_f, hour_f, dom_f, _month_f, dow_f = fields
    minutes = _expand(minute_f, 0, 59)
    hours = _expand(hour_f, 0, 23)
    days = set(range(7)) if dom_f not in ("*", "?") else {d % 7 for d in _expand(dow_f, 0, 7)}

    problems = []
    if CRON_MONDAY in days and min(hours) < MONDAY_BLOCK_ENDS_HOUR:
        problems.append(
            f"{schedule!r} can fire on a Monday before {MONDAY_BLOCK_ENDS_HOUR}:00 Chicago (Bank Health's run)"
        )
    if any(_in_reboot_window(h, m) for h in hours for m in minutes):
        problems.append(f"{schedule!r} can fire between 02:30 and 04:00 Chicago (VM reboot window)")
    return problems


def blocked_reason(now: datetime) -> str | None:
    """Why `now` is in a blocked window, or None if a run may start."""
    local = now.astimezone(TZ)
    if local.weekday() == MONDAY and local.hour < MONDAY_BLOCK_ENDS_HOUR:
        return "Monday before 13:00 Chicago is Bank Health's window on the shared VM and Databricks workspace"
    if _in_reboot_window(local.hour, local.minute):
        return "02:30 to 04:00 Chicago is the VM's automatic reboot window"
    return None


def refuse_blocked_window(now: datetime | None = None) -> None:
    """Called first by every task: stop if this moment is in a blocked window."""
    moment = now or datetime.now(TZ)
    reason = blocked_reason(moment)
    if reason:
        raise BlockedWindow(
            f"{moment.astimezone(TZ):%a %Y-%m-%d %H:%M} America/Chicago: {reason}. Not starting."
        )
