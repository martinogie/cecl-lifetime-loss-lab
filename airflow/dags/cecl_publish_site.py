"""
### CECL Lifetime Loss Lab: publish the website's data (Phase 7)

Runs at the end of a successful run: triggered by the quarterly close once every check has passed (Phase 8
wires the trigger; it can also be started by hand). No schedule of its own, so it never starts in the blocked
windows on its own; the task checks them anyway (`cecl/scheduling.py`). Paused when first loaded.

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `publish` | builds the six site data files from the run's aggregates (`ingestion/site_export.py`), validates them against the site's JSON Schemas, scans them for loan ids and identifiers, uploads them to a new run folder in the private R2 bucket, checks every object, then switches `current.json`; keeps the newest 5 runs; writes the run-log record and the success alert | each run writes its own folder; the switch is one small file; a failure before it leaves the live site on the previous run |

The finance router (portfolio-site) serves `finance.martinsogiekpolor.com/cecl/data/*` from the run named in
`current.json`, so the site refreshes without a rebuild. Prod publishes at the bucket's root (what the site
serves); dev under `dev/` (never served). Needs `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and
`R2_ENDPOINT` in this stack's .env (Martins adds them; an R2 token with object read and write on the
`cecl-data` bucket only; never printed). Rollback: `python -m ingestion.site_publish rollback <run key>`.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import pendulum
from airflow.sdk import dag, task

from cecl import environment
from ingestion.alerts import failure_callback
from ingestion.preflight import task_preflight

ENV = environment.require_airflow_env()
DATA_ROOT = Path("/opt/airflow/data")


@dag(
    dag_id="cecl_publish_site",
    schedule=None,  # triggered by the quarterly close after every check passes (Phase 8), or by hand
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name, "site"],
    doc_md=__doc__,
    default_args={
        "on_failure_callback": failure_callback(DATA_ROOT, "site_publish"),
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    },
)
def cecl_publish_site() -> None:
    @task
    def publish() -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from ingestion import alerts, run_log, site_publish

        task_preflight()
        context = get_current_context()
        record = run_log.new_record("site_publish", {"airflow_run_id": context["run_id"]})
        started = context["dag_run"].start_date
        if started is not None:
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        summary = site_publish.run(DATA_ROOT, context["run_id"], started or pendulum.now("UTC"))
        record.details = {k: summary[k] for k in ("run", "files", "previous", "deleted")}
        run_log.finish(record)
        record.alerts = alerts.send(alerts.success_text(
            context["dag"].dag_id, context["run_id"], record.duration_seconds,
            f"site data published: run {summary['run']} ({summary['files']} files); previous {summary['previous']}"))  # fmt: skip
        run_log.write(DATA_ROOT, record)
        return {k: summary[k] for k in ("run", "files", "previous", "deleted")}

    publish()


cecl_publish_site()
