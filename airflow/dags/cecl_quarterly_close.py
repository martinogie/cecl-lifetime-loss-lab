"""
### CECL Lifetime Loss Lab: quarterly close (Phase 8)

Started by Martins after Freddie's quarterly release (no schedule): he downloads the new sample files in the
browser, uploads them to the prod volume (ADR-001), records the quarter's overlay review (ADR-006), then
triggers this DAG with the quarter-end to close (`reserve_ym`, e.g. `202609`) and Freddie's release number.
Paused when first loaded.

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `preflight` | refuses if a blocked window falls within the next 4 hours; checks the quarter-end (next after the last close), the quarter's overlay review in the committed log, that every prod job was deployed from the VM's commit, and that every year's Freddie file is in the volume and newer than the last close | reads only |
| `economic_tables` | uploads the VM's economic tables to the volume; job `load_macro_tables` loads them (row tie-outs) | tables replaced whole |
| `freddie_bronze` | job `bronze_load`: Freddie zips -> bronze (data contracts, row tie-outs) | each vintage replaced |
| `panel` | job `panel_build`: silver -> gold loan panel, then the C02 tie-outs | rebuilt whole |
| `projection` | job `reserve_projection`: the `@challenger` models play the book at R forward (R and prior economies) | files overwritten |
| `pricing` | uploads the committed overlay log; job `close_pricing`: pricing, weighting, rollforward from the previous close's booked frame, bridge, disclosures, live rolling backtest, quarter-end snapshot; any failed control stops it | writes only its own quarter's folder |
| `monitoring` | job `monitoring` (quarterly): calibration per transition, AUC, trend, retrain check | its quarter's record replaced |
| `finish` | copies the close record and monitoring to the VM, drafts the committee pack, writes the run log, sends the alert (WARNING when monitoring flags a cell or a retrain) | files overwritten |

Heavy jobs never retry automatically: a failure alerts and stops, so nothing runs on top of a problem. The
results reach the public repo through a reviewed PR (committee pack with the sign-off page); the site is
published after that (`cecl_publish_site`). Needs `DATABRICKS_HOST` and `DATABRICKS_TOKEN` in the stack's .env
(Martins adds them; never printed or logged). Only aggregates come back to the VM.

**Schedule rules (shared VM):** no task starts on a Monday before 13:00 Chicago or between 02:30 and 04:00
(`cecl/scheduling.py`), and the close does not start if one of those windows begins within 4 hours.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pendulum
from airflow.sdk import Param, dag, task

from cecl import environment
from cecl.scheduling import TZ
from ingestion.alerts import failure_callback
from ingestion.preflight import task_preflight

ENV = environment.require_airflow_env()
DATA_ROOT = Path("/opt/airflow/data")
DOCS = Path("/opt/airflow/docs")
GIT_DIR = Path("/opt/airflow/repo.git")  # the repository's .git, mounted read-only (the VM's commit)


def _ws() -> Any:
    from ingestion import databricks_api

    return databricks_api.workspace()


def _job(name: str) -> str:
    from ingestion import databricks_api

    return databricks_api.job_name(name, ENV.name)


@dag(
    dag_id="cecl_quarterly_close",
    schedule=None,  # started by Martins after Freddie's release and the quarter's overlay review
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["cecl", ENV.name, "close"],
    doc_md=__doc__,
    params={
        "reserve_ym": Param("", type="string", description="Quarter-end to close, YYYYMM (e.g. 202609)"),
        "freddie_release": Param(
            "48", type="string", description="Freddie's release number (bronze lineage)"
        ),
    },
    default_args={"on_failure_callback": failure_callback(DATA_ROOT, "close"), "retries": 0},
)
def cecl_quarterly_close() -> None:
    @task
    def preflight() -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from ingestion import close_run

        task_preflight()
        params = get_current_context()["params"]
        close_run.check_time_budget(datetime.now(TZ))
        ws = _ws()
        last = close_run.last_close(ws)
        R = close_run.check_request(str(params["reserve_ym"]), datetime.now(TZ).date(), last)
        reviews = close_run.check_overlay_review(DOCS / "governance" / "overlay_log.json", R)
        commit = close_run.vm_commit(GIT_DIR)
        close_run.check_deploys(ws, ENV.name, commit)
        newer_than = None
        if last:
            rec = [
                e
                for e in ws.list_dir(f"{close_run.volume()}/results/close/{last}")
                if e["name"] == "close.json"
            ]
            newer_than = datetime.fromtimestamp(rec[0]["last_modified"] / 1000, UTC) if rec else None
        freddie = close_run.check_freddie(ws, range(1999, int(R[:4]) + 1), newer_than)
        return {"R": R, "prior": last, "commit": commit, "years": f"1999-{R[:4]}",
                "release": str(params["freddie_release"]), "freddie": freddie, "overlay_reviews": reviews}  # fmt: skip

    @task
    def economic_tables(info: dict[str, Any]) -> dict[str, Any]:
        from ingestion import close_run

        task_preflight()
        ws = _ws()
        sent = close_run.upload_macro(ws, DATA_ROOT)
        return ws.run_and_wait(_job("load_macro_tables"), {"tables": ",".join(sent)}) | {"tables": sent}

    @task
    def freddie_bronze(info: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        task_preflight()
        return dict(
            _ws().run_and_wait(_job("bronze_load"), {"years": info["years"], "release": info["release"]})
        )

    @task
    def panel(info: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        task_preflight()
        return dict(_ws().run_and_wait(_job("panel_build"), {"years": info["years"]}))

    @task
    def projection(info: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        from ingestion import close_run

        task_preflight()
        ws = _ws()
        params = close_run.projection_params(ws, info["R"]) | {"code_version": info["commit"]}
        return dict(ws.run_and_wait(_job("reserve_projection"), params, timeout=6 * 3600))

    @task
    def pricing(info: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        from ingestion import close_run

        task_preflight()
        ws = _ws()
        log = (DOCS / "governance" / "overlay_log.json").read_bytes()
        ws.upload(log, f"{close_run.volume()}/governance/overlay_log.json")
        return dict(
            ws.run_and_wait(_job("close_pricing"), {"reserve_ym": info["R"], "code_version": info["commit"]})
        )

    @task
    def monitoring(info: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        task_preflight()
        return dict(_ws().run_and_wait(_job("monitoring"), {"mode": "quarterly", "reserve_ym": info["R"],
                                                            "code_version": info["commit"]}))  # fmt: skip

    @task
    def finish(info: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
        from airflow.sdk import get_current_context

        from ingestion import alerts, close_run, committee_pack, run_log

        task_preflight()
        context = get_current_context()
        files = close_run.fetch(_ws(), info["R"], DATA_ROOT)
        rec = json.loads(files["close"].read_text(encoding="utf-8"))
        mon = json.loads(files["monitoring"].read_text(encoding="utf-8"))
        pack = files["close"].parent / "committee_pack.md"
        pack.write_text(committee_pack.render(rec, mon), encoding="utf-8")
        record = run_log.new_record("close", {"airflow_run_id": context["run_id"], "reserve_ym": info["R"]})
        started = context["dag_run"].start_date
        if started is not None:
            record.started_at = started.astimezone(UTC).replace(microsecond=0).isoformat()
        record.details = {"commit": info["commit"], "freddie": info["freddie"],
                          "databricks_minutes": {j["run_id"]: j["minutes"] for j in jobs},
                          "total_allowance": rec["dates"][info["R"]]["total_allowance"], "controls": rec["controls"]}  # fmt: skip
        run_log.finish(record)
        flagged = bool(mon.get("alerts")) or bool((mon.get("retrain") or {}).get("due"))
        text = close_run.summary_line(rec, mon) + "".join(f"\n- {a}" for a in mon.get("alerts", [])[:10])
        record.alerts = alerts.send(alerts.success_text(context["dag"].dag_id, context["run_id"],
                                                        record.duration_seconds, text, warning=flagged))  # fmt: skip
        run_log.write(DATA_ROOT, record)
        return {"reserve_ym": info["R"], "total_allowance": rec["dates"][info["R"]]["total_allowance"],
                "committee_pack": str(pack), "flagged": flagged}  # fmt: skip

    info = preflight()
    m = economic_tables(info)
    b = freddie_bronze(info, m)
    p = panel(info, b)
    pr = projection(info, p)
    c = pricing(info, pr)
    mo = monitoring(info, c)
    finish(info, [m, b, p, pr, c, mo])


cecl_quarterly_close()
