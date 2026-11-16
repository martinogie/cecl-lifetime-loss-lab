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

A contract failure fails its task before bronze is written, and `silver` doesn't run. Stale series
are listed in the task's result (alerts arrive in step 8). Freddie Mac files are never downloaded
here (ADR-001): only public economic data.

**Schedule rules (shared VM):** no run starts on a Monday before 13:00 Chicago or between 02:30 and
04:00 any day: checked in CI for the schedule, and by every task at start (`cecl/scheduling.py`).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pendulum
from airflow.sdk import dag, task

from cecl import environment
from cecl.scheduling import TZ
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
        from ingestion import macro_build

        task_preflight()
        result = macro_build.build_silver(DATA_ROOT)
        stale = {k: v for load in loads for k, v in load["stale"].items()}
        result["stale"] = stale
        print("stale series:", stale or "none")
        return result

    silver([fred(), fhfa(), fed_scenarios()])


cecl_macro_data()
