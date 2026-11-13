"""The first call of every Airflow task.

Two checks, before a task touches anything:
1. CECL_ENV is set and is dev or prod (the DAG file checks this at import too, but a task re-checks
   because it runs in its own process and the environment could differ).
2. It is not a blocked window on the shared VM: Monday before 13:00 Chicago (Bank Health's weekly
   run) or 02:30 to 04:00 any day (the VM's reboot window). See cecl/scheduling.py.
Either failure raises, so the task fails loudly instead of running in the wrong place or time.
"""

from __future__ import annotations

from datetime import datetime

from cecl import environment, scheduling


def task_preflight(now: datetime | None = None) -> environment.Settings:
    settings = environment.require_airflow_env()
    scheduling.refuse_blocked_window(now)
    print("CECL task preflight ok:", environment.describe())
    return settings
