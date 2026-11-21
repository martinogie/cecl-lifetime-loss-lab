"""
### CECL Lifetime Loss Lab: weekly health check

Wednesdays 07:00 America/Chicago. Paused when first loaded.

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
| `check_environment` | logs which environment, catalog and dbt target this stack uses; with the manual-run option `simulate_failure` (dev only) it fails on purpose, to test the failure alert | read-only |
| `check_freshness` | reads the run log: weekly pipelines that haven't succeeded in 8 days, latest runs that failed, stale sources; checks the FFIEC PWS token's expiry (WARNING from 14 days before); sends the run's alert (OK, or WARNING listing the problems) and writes its own run-log record | read-only, apart from its own record |

Every run ends with one alert: OK or WARNING from `check_freshness`, or FAILED from whichever task
failed (`on_failure_callback`). Problems are reported as WARNING, not raised: the check's job is to
tell someone (`ingestion/health.py`). Alerts skip cleanly when no webhook is set.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import Param, dag, task

from cecl import environment
from ingestion.alerts import failure_callback
from ingestion.preflight import task_preflight

# Fails the DAG import if CECL_ENV is missing or not dev/prod. Nothing runs with a guessed environment.
ENV = environment.require_airflow_env()

SCHEDULE = "0 7 * * 3"  # Wednesday 07:00 Chicago; clear of both blocked windows (checked in CI)
DATA_ROOT = Path("/opt/airflow/data")  # the data folder mounted by docker-compose.yaml


@dag(
    dag_id="cecl_health_check",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name],
    doc_md=__doc__,
    # One retry after 2 minutes absorbs a momentary hiccup (every task is safe to repeat); the
    # FAILED alert goes out only if the retry fails too.
    default_args={
        "on_failure_callback": failure_callback(DATA_ROOT, "health_check"),
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    # Testing the failure alert: trigger by hand with simulate_failure = true (dev only).
    params={"simulate_failure": Param(False, type="boolean", description="dev only: fail on purpose")},
)
def cecl_health_check() -> None:
    @task
    def check_environment(params: dict | None = None) -> str:
        task_preflight()
        if params and params.get("simulate_failure"):
            if ENV.name != "dev":
                raise ValueError("simulate_failure is refused outside dev")
            raise RuntimeError("simulated failure (manual test of the failure alert)")
        return environment.describe()

    @task
    def check_freshness(_: str) -> list[str]:
        from datetime import UTC, datetime

        from airflow.sdk import get_current_context

        from ingestion import alerts, health, run_log

        task_preflight()
        context = get_current_context()
        record = run_log.new_record("health_check", {"airflow_run_id": context["run_id"]})
        started = context["dag_run"].start_date
        if started is not None:
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        found = health.problems(run_log.read_all(DATA_ROOT), datetime.now(UTC))
        # FFIEC PWS token (Phase 6 peer data): warn 14 days before it expires, and once it has expired
        from cecl.scheduling import TZ
        from ingestion import ffiec

        token = ffiec.token_warning(datetime.now(TZ).date())
        if token:
            found.append(token)
        record.details = {"problems": found}
        # Every Airflow run ends with one alert: OK, or WARNING listing what was found.
        run_log.finish(record)
        summary = "; ".join(found) if found else "no problems (run log, freshness, latest runs)"
        record.alerts = alerts.send(
            alerts.success_text(
                context["dag"].dag_id,
                context["run_id"],
                record.duration_seconds,
                summary,
                warning=bool(found),
            )  # fmt: skip
        )
        run_log.write(DATA_ROOT, record)
        print("health check problems:", found or "none")
        return found

    check_freshness(check_environment())


cecl_health_check()
