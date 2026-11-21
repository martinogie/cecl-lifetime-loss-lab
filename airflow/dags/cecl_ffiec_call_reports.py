"""
### CECL Lifetime Loss Lab: FFIEC call reports for the peer comparison (Phase 6)

Quarterly, on the 15th of February, May, August and November at 14:00 America/Chicago, about 45 days
after each quarter-end, once the banks' call reports are public. Paused when first loaded.

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `call_reports` | checks the FFIEC PWS token (fails with a clear message if expired); downloads Schedule RI-C for every RI-C filer ($1bn+ assets) for the initial periods and the latest quarter, through the official FFIEC Public Web Service, politely (one call every 2 seconds, retries with back-off); checks the data contract; writes the aggregates (median and range by bank size) to `data/local/silver/ffiec_peer_ratios.json`; writes the run-log record and the success alert (WARNING if the token expires within 14 days) | each bank-period is cached once and skipped afterwards; aggregates are rebuilt whole |

Needs `FFIEC_PWS_USERNAME` and `FFIEC_PWS_TOKEN` in this stack's .env (Martins adds them; never printed
or logged). Only aggregates are kept beyond the cache; no bank-level data is published. Bank Health is
not touched.

**Schedule rules (shared VM):** no run starts on a Monday before 13:00 Chicago or between 02:30 and
04:00 any day: checked in CI for the schedule, and by every task at start (`cecl/scheduling.py`).
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

SCHEDULE = "0 14 15 2,5,8,11 *"  # 15th of Feb/May/Aug/Nov, 14:00 Chicago: clear of both blocked windows
DATA_ROOT = Path("/opt/airflow/data")


@dag(
    dag_id="cecl_ffiec_call_reports",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name, "peer"],
    doc_md=__doc__,
    default_args={
        "on_failure_callback": failure_callback(DATA_ROOT, "ffiec"),
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(hours=8),  # the first load is about 7,700 calls at one every 2 seconds
    },
)
def cecl_ffiec_call_reports() -> None:
    @task
    def call_reports() -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from ingestion import alerts, ffiec, run_log

        task_preflight()
        context = get_current_context()
        today = datetime.now(TZ).date()
        contract = ffiec.load_contract()
        periods = ffiec.periods_from(contract, [ffiec.latest_quarter_end(today)])
        record = run_log.new_record("ffiec", {"airflow_run_id": context["run_id"]})
        started = context["dag_run"].start_date
        if started is not None:
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        summary = ffiec.run(DATA_ROOT, periods, today)
        out = DATA_ROOT / "local" / "silver" / "ffiec_peer_ratios.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"periods": summary["periods"], "aggregates": summary["aggregates"]}, indent=1)
        )
        record.rows.update({f"ffiec_ok_{p}": c["ok"] for p, c in summary["periods"].items()})
        record.contracts.append(
            {"file": "ffiec_call", "contract": "pass", "checked": summary["bank_periods_ok"]}
        )
        record.details = {"token": summary["token"], "api_calls": summary["api_calls"]}
        run_log.finish(record)
        token = summary["token"]
        warn = token["status"] in ("expiring", "unknown")
        text = (f"{summary['bank_periods_ok']:,} bank-periods ok over {len(periods)} periods; "
                f"{summary['api_calls']:,} API calls; token {token['status']}"
                + (f" ({token['days_left']} days left)" if token["days_left"] is not None else ""))  # fmt: skip
        record.alerts = alerts.send(alerts.success_text(context["dag"].dag_id, context["run_id"],
                                                        record.duration_seconds, text, warning=warn))  # fmt: skip
        run_log.write(DATA_ROOT, record)
        return {"periods": summary["periods"], "token": token}

    call_reports()


cecl_ffiec_call_reports()
