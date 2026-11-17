"""Run a Databricks job with parameters, wait for it, and record its minutes (Phase 2).

    CECL_ENV=local python -m scripts.run_job --job cecl_lab_train_family_dev \
        --param family=logistic --param 'settings={"C": 1.0}' --param code_version=<sha>

Uses the Jobs API (`jobs run-now --json`), so a value may contain commas, unlike the CLI's
`--params` flag. Refuses in the blocked windows (Monday before 13:00 Chicago, 02:30-04:00), waits
for the run, prints each task's notebook output, and writes the run's minutes to the run log
(scripts/record_job_minutes.py), success or failure.

Every run points to an exact commit (Martins, 17 Nov 2026): it refuses if tracked files have
uncommitted changes, deploys the bundle itself from that clean commit (so Databricks runs exactly the
committed code), and passes the full commit hash as `code_version` to jobs that take it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any

from cecl import environment, scheduling
from scripts import record_job_minutes

PROFILE = "cecl"
POLL_SECONDS = 30
TAKES_CODE_VERSION = ("cecl_lab_train_family_dev", "cecl_lab_oot_test_dev")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def deploy() -> None:
    """Deploy the bundle from the clean working tree (the wheel is built with the project's Python)."""
    env = os.environ | {
        "PATH": os.path.abspath(os.path.join(".venv", "Scripts")) + os.pathsep + os.environ["PATH"]
    }
    out = subprocess.run(["databricks", "bundle", "deploy", "-t", "dev", "--profile", PROFILE], cwd="databricks",
                         env=env, capture_output=True, text=True)  # fmt: skip
    if out.returncode != 0:
        raise SystemExit("bundle deploy failed: " + (out.stdout + out.stderr)[-1500:])


def cli(*args: str) -> Any:
    out = subprocess.run(["databricks", *args, "--profile", PROFILE, "-o", "json"], capture_output=True, text=True,
                         check=True).stdout  # fmt: skip
    return json.loads(out) if out.strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--job", required=True)
    parser.add_argument("--param", action="append", default=[], help="name=value (repeatable)")
    args = parser.parse_args(argv)
    environment.current_env()
    scheduling.refuse_blocked_window()  # the workspace and its allowance are shared with Bank Health
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(
            "uncommitted changes to tracked files: commit first, so the run points to an exact commit"
        )
    commit = git("rev-parse", "HEAD")
    deploy()
    params = dict(p.split("=", 1) for p in args.param)
    if args.job in TAKES_CODE_VERSION:
        params["code_version"] = commit
    job_id = next(j["job_id"] for j in cli("jobs", "list") if j["settings"]["name"] == args.job)
    run_id = cli("jobs", "run-now", "--json", json.dumps({"job_id": job_id, "job_parameters": params}),
                 "--no-wait")["run_id"]  # fmt: skip
    print(f"started {args.job} run {run_id} at commit {commit[:12]} with {params}", flush=True)
    while True:
        run = cli("jobs", "get-run", str(run_id))
        state = run["state"]
        if state.get("life_cycle_state") in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            break
        time.sleep(POLL_SECONDS)
    print(
        f"finished: {state.get('result_state') or state.get('life_cycle_state')} {state.get('state_message', '')[:200]}"
    )
    for task in run.get("tasks", []):
        try:
            out = cli("jobs", "get-run-output", str(task["run_id"]))
            result = (out.get("notebook_output") or {}).get("result") or out.get("error", "")[:500]
            print(f"  {task['task_key']}: {result}")
        except subprocess.CalledProcessError:
            print(f"  {task['task_key']}: (no output)")
    record_job_minutes.main(["--job", args.job, "--last", "1"])
    return 0 if state.get("result_state") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
