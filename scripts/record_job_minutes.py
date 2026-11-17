"""Record the latest completed run(s) of a Databricks job in the run log, with their compute minutes.

    CECL_ENV=local python -m scripts.record_job_minutes --job cecl_lab_train_family_dev [--last 1]

The allowance rule (Phase 2 plan, L-009): every Databricks run's minutes go into the run log,
failed runs included. Reads the Jobs API through the CLI profile `cecl` (no compute). A run already
recorded is skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from cecl import environment
from ingestion import run_log

PROFILE = "cecl"


def cli(*args: str) -> Any:
    out = subprocess.run(["databricks", *args, "--profile", PROFILE, "-o", "json"], capture_output=True, text=True,
                         check=True).stdout  # fmt: skip
    return json.loads(out) if out.strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--job", required=True)
    parser.add_argument("--last", type=int, default=1)
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    environment.current_env()
    done = {
        r["params"].get("job_run_id")
        for r in run_log.read_all(args.root)
        if r["pipeline"].startswith("databricks")
    }
    job_id = next(j["job_id"] for j in cli("jobs", "list") if j["settings"]["name"] == args.job)
    runs = (
        cli("jobs", "list-runs", "--job-id", str(job_id), "--completed-only", "--limit", str(args.last)) or []
    )
    for r in sorted(runs, key=lambda r: r["start_time"]):
        if r["run_id"] in done:
            print(f"run {r['run_id']} already recorded")
            continue
        full = cli("jobs", "get-run", str(r["run_id"]))
        tasks = {
            t["task_key"]: round(t.get("execution_duration", 0) / 60000, 2) for t in full.get("tasks", [])
        }
        state = full["state"].get("result_state")
        params = {p["name"]: p.get("value", p.get("default")) for p in full.get("job_parameters", [])}
        rec = run_log.new_record(
            "databricks_job", {"job": args.job, "job_run_id": r["run_id"], "job_parameters": params}
        )
        rec.databricks_minutes = round(sum(tasks.values()), 2)
        rec.details = {"minutes_by_task": tasks, "result": state}
        run_log.write(
            args.root, run_log.finish(rec, None if state == "SUCCESS" else RuntimeError(str(state)))
        )
        print(f"run {r['run_id']}: {state}, {rec.databricks_minutes} minutes {tasks}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
