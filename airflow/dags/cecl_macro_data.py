"""
### CECL Lifetime Loss Lab: economic data (Phase 1 step 7)

Wednesdays 08:00 America/Chicago. Paused when first loaded. Downloads the public economic series,
checks each against its data contract, and builds the monthly silver tables
(`docs/phase1/step7_economic_data.md`).

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `fred` | national unemployment, mortgage rate, Treasury yields, 3-month bill, 51 state unemployment series (needs `FRED_API_KEY`) | raw files for the day are overwritten; bronze replaced atomically |
| `fhfa` | FHFA all-transactions house price indexes, 50 states, DC and the US | same |
| `fed_scenarios` | the Fed's 2026 baseline and severely adverse domestic paths | same |
| `silver` | monthly national and state tables, long-run averages (1990 on), Fed paths anchored on June 2026 | silver replaced atomically from bronze |

A contract failure fails its task before bronze is written, and `silver` doesn't run. `silver`
writes the run-log record (`data/local/ops/run_log/`) and sends the run's success alert (OK, or
WARNING if a series is stale); any failed task alerts and writes a failed record (`ingestion/alerts.py`; alerts skip cleanly when no webhook
is set). Freddie Mac files are never downloaded here (ADR-001): only public economic data.

**Schedule rules (shared VM):** no run starts on a Monday before 13:00 Chicago or between 02:30 and
04:00 any day: checked in CI for the schedule, and by every task at start (`cecl/scheduling.py`).
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

SCHEDULE = "0 8 * * 3"  # Wednesday 08:00 Chicago, after the health check; clear of the blocked windows
DATA_ROOT = Path("/opt/airflow/data")  # the data folder mounted by docker-compose.yaml


def _run_date() -> Any:
    return datetime.now(TZ).date()


@dag(
    dag_id="cecl_macro_data",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name, "macro"],
    doc_md=__doc__,
    # One retry after 2 minutes absorbs a momentary hiccup (every task is safe to repeat); the
    # FAILED alert goes out only if the retry fails too.
    default_args={
        "on_failure_callback": failure_callback(DATA_ROOT, "macro"),
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
)
def cecl_macro_data() -> None:
    @task
    def fred() -> dict[str, Any]:
        from ingestion import macro_build

        task_preflight()
        return macro_build.load_fred(DATA_ROOT, _run_date())

    @task
    def fhfa() -> dict[str, Any]:
        from ingestion import macro_build

        task_preflight()
        return macro_build.load_fhfa(DATA_ROOT, _run_date())

    @task
    def fed_scenarios() -> dict[str, Any]:
        from ingestion import macro_build

        task_preflight()
        return macro_build.load_fed(DATA_ROOT, _run_date())

    @task
    def silver(loads: list[dict[str, Any]]) -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from ingestion import alerts, macro_build, run_log

        task_preflight()
        context = get_current_context()
        record = run_log.new_record("macro", {"airflow_run_id": context["run_id"]})
        started = context["dag_run"].start_date
        if started is not None:  # the record covers the whole DAG run, not just this task
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        result = macro_build.build_silver(DATA_ROOT)
        summaries = {load["source"]: load for load in loads}
        stale = macro_build.stale_sources(summaries)
        result["stale"] = stale
        for name, load in summaries.items():
            record.rows[f"bronze_macro_{name}"] = load["bronze_rows"]
            record.contracts.append({"file": f"macro_{name}", "contract": load["contract"],
                                     "checked": load["series_checked"]})  # fmt: skip
        record.rows.update({f"silver_macro_{t}": n for t, n in result["silver_rows"].items()})
        record.stale = stale
        record.details = {k: result[k] for k in ("anchors", "severely_adverse_worst", "fed_timing")}
        # Every Airflow run ends with one alert: this success line (failures alert from the
        # failing task's on_failure_callback). Stale data makes it a WARNING, not a failure.
        run_log.finish(record)
        checked = sum(load["series_checked"] for load in loads)
        summary = (
            f"{checked} checks pass (FRED, FHFA, Fed); silver "
            f"{', '.join(f'{t} {n:,}' for t, n in result['silver_rows'].items())}; "
            f"stale: {sorted(stale) if stale else 'none'}"
        )
        record.alerts = alerts.send(
            alerts.success_text(
                context["dag"].dag_id,
                context["run_id"],
                record.duration_seconds,
                summary,
                warning=bool(stale),
            )  # fmt: skip
        )
        run_log.write(DATA_ROOT, record)
        print("stale series:", stale or "none", "| run log:", record.run_id)
        return result

    silver([fred(), fhfa(), fed_scenarios()])


cecl_macro_data()
