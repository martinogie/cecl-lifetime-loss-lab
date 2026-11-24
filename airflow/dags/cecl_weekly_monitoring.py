"""
### CECL Lifetime Loss Lab: weekly model monitoring (Phase 8)

Wednesdays 09:00 America/Chicago, after the economic data (08:00). Paused when first loaded.

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `weekly` | reads each source's newest month from what the VM holds (freshness: no Databricks minutes); if a new economic month has arrived since the last scored week, runs the Databricks job `monitoring` (weekly): the live book scored with the latest economy, PSI per transition against the baseline profile; writes the week's record (green / amber / red) and the alert (WARNING unless green) | the week's record is replaced; the scored-month state moves on only after a successful scoring |

Rules: `cecl/monitoring.py` (roadmap v2 changes, approved 18 November 2026). Needs `DATABRICKS_HOST` and
`DATABRICKS_TOKEN` only when it scores. Aggregates only.

**Schedule rules (shared VM):** no run starts on a Monday before 13:00 Chicago or between 02:30 and 04:00 any
day: checked in CI for the schedule, and by the task at start (`cecl/scheduling.py`).
"""

from __future__ import annotations

import json
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
SCHEDULE = "0 9 * * 3"  # Wednesday 09:00 Chicago, after cecl_macro_data (08:00); clear of the blocked windows
DATA_ROOT = Path("/opt/airflow/data")


@dag(
    dag_id="cecl_weekly_monitoring",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name, "monitoring"],
    doc_md=__doc__,
    default_args={
        "on_failure_callback": failure_callback(DATA_ROOT, "monitoring"),
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },  # fmt: skip
)
def cecl_weekly_monitoring() -> None:
    @task
    def weekly() -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from cecl import monitoring
        from ingestion import alerts, databricks_api, monitor_run, run_log

        task_preflight()
        context = get_current_context()
        today = datetime.now(TZ).date()
        record = run_log.new_record("monitoring", {"airflow_run_id": context["run_id"]})
        started = context["dag_run"].start_date
        if started is not None:
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        latest = monitor_run.latest_months(DATA_ROOT)
        scored = None
        if monitoring.needs_scoring(monitor_run.economy_month(latest), monitor_run.last_scored(DATA_ROOT)):
            out = databricks_api.workspace().run_and_wait(
                databricks_api.job_name("monitoring", ENV.name), {"mode": "weekly"}
            )
            scored = json.loads(out["tasks"]["monitoring"])
            record.details = {"databricks_minutes": out["minutes"]}
        rec = monitor_run.record(DATA_ROOT, today, latest, scored)
        run_log.finish(record)
        record.alerts = alerts.send(alerts.success_text(context["dag"].dag_id, context["run_id"], record.duration_seconds,
                                                        monitor_run.summary_line(rec), warning=rec["status"] != "green"))  # fmt: skip
        run_log.write(DATA_ROOT, record)
        return {"status": rec["status"], "scored": rec["scored"]}

    weekly()


cecl_weekly_monitoring()
