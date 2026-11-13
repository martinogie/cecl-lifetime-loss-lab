"""
### CECL Lifetime Loss Lab: weekly health check (Phase 0 placeholder)

Wednesdays 07:00 America/Chicago. Paused when first loaded; nothing is deployed in Phase 0.

**Environment:** `CECL_ENV` (dev or prod, set in this stack's Compose file) is required. If it is
missing or wrong, this DAG fails to load (shown in the Airflow UI), and every task checks it again
before doing anything (`ingestion/preflight.py`).

**Schedule rules (shared VM):** prod runs on the Oracle VM shared with Bank Health, whose weekly run
starts Mondays at 06:00 CT and must finish by noon. No CECL run may start on a Monday before 13:00
Chicago, or between 02:30 and 04:00 on any day (the VM's automatic reboot window). CI rejects any
DAG whose schedule can fire in those windows, and every task refuses to start in them even when
triggered by hand (`cecl/scheduling.py`).

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `check_environment` | logs which environment, catalog and dbt target this stack uses | read-only |

Phase 1 adds the data health checks (freshness, run log) to this DAG.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import dag, task

from cecl import environment
from ingestion.preflight import task_preflight

# Fails the DAG import if CECL_ENV is missing or not dev/prod. Nothing runs with a guessed environment.
ENV = environment.require_airflow_env()

SCHEDULE = "0 7 * * 3"  # Wednesday 07:00 Chicago; clear of both blocked windows (checked in CI)


@dag(
    dag_id="cecl_health_check",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name],
    doc_md=__doc__,
)
def cecl_health_check() -> None:
    @task
    def check_environment() -> str:
        task_preflight()
        return environment.describe()

    check_environment()


cecl_health_check()
