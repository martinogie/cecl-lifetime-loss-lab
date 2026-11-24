"""
### CECL Lifetime Loss Lab: quarterly release reminder (Phase 8)

14:00 America/Chicago on the 15th of January, April, July and October (14:00 because the 15th is sometimes
a Monday, when the morning is Bank Health's window). Paused when first loaded.

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `remind` | posts one message to the `[CECL]` Slack and Discord channels: Freddie's quarterly loan data is due, with the quarter-end to close and the steps (download in the browser, upload, overlay review, trigger `cecl_quarterly_close`); writes the run-log record | a second message is harmless |

A reminder only: it never checks Freddie's site or downloads anything (Freddie's terms do not cover scripted
downloads, ADR-001). No Databricks minutes. The text is in `ingestion/reminders.py`.

**Schedule rules (shared VM):** no run starts on a Monday before 13:00 Chicago or between 02:30 and 04:00 any
day: checked in CI for the schedule, and by the task at start (`cecl/scheduling.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pendulum
from airflow.sdk import dag, task

from cecl import environment
from cecl.scheduling import TZ
from ingestion.alerts import failure_callback
from ingestion.preflight import task_preflight

ENV = environment.require_airflow_env()
# 14:00 Chicago on the 15th of Freddie's release months: the 15th is sometimes a Monday (e.g. 15 January 2029),
# when anything before 13:00 is Bank Health's window, so the hour is 14:00 every time.
SCHEDULE = "0 14 15 1,4,7,10 *"
DATA_ROOT = Path("/opt/airflow/data")


@dag(
    dag_id="cecl_quarterly_reminder",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name, "reminder"],
    doc_md=__doc__,
    default_args={
        "on_failure_callback": failure_callback(DATA_ROOT, "reminder"),
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },  # fmt: skip
)
def cecl_quarterly_reminder() -> None:
    @task
    def remind() -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from ingestion import alerts, reminders, run_log

        task_preflight()
        context = get_current_context()
        record = run_log.new_record("reminder", {"airflow_run_id": context["run_id"]})
        started = context["dag_run"].start_date
        if started is not None:
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        today = datetime.now(TZ).date()
        record.alerts = alerts.send(reminders.text(today))
        run_log.write(DATA_ROOT, run_log.finish(record))
        return {"quarter_to_close": reminders.quarter_to_close(today), "alerts": record.alerts}

    remind()


cecl_quarterly_reminder()
