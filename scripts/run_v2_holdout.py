"""Start the model v2 holdout, once (ADR-009 Amendment 3 E; locked by Amendment 5).

    CECL_ENV=dev python -m scripts.run_v2_holdout --check-only   # every guard, starts nothing
    CECL_ENV=dev python -m scripts.run_v2_holdout                # guards, deploy, start the job, wait

Refuses unless every guard passes:
1. ADR-009 carries Amendment 5 with Martins's Decision and the lock ("the holdout rules are locked");
2. no uncommitted changes to tracked files (the holdout names exact code);
3. Martins's written expectation is marked final (docs/phase2/model_v2_holdout_expectations.md);
4. each step's v2 @challenger (`<catalog>.ml.pd_v2_transition_<step>`) exists;
5. the downturn check passed for those runs (the check itself, or its Amendment 5 re-evaluation);
6. no v2 holdout run exists yet (the job checks again before scoring);
7. the time is outside the blocked windows (never Monday before 13:00 Chicago, never 02:30-04:00).
Then it deploys the bundle from the clean commit, starts the `holdout_v2` job and waits for it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cecl import environment, scheduling, tracking
from cecl.models import oot

ADR = Path("docs/adr/009-model-selection-rule.md")
EXPECTATIONS = Path("docs/phase2/model_v2_holdout_expectations.md")
JOB = "cecl_lab_holdout_v2_dev"
PROFILE = "cecl"
LOCK = "the holdout rules are locked"


def document_problems(adr: str, expectations: str) -> list[str]:
    """Guards 1 and 3, on the documents' text."""
    problems = []
    a5 = adr.split("## Amendment 5", 1)
    if (
        len(a5) < 2
        or "**Status:** accepted" not in a5[1]
        or "Decision (Martins" not in a5[1]
        or LOCK not in a5[1]
    ):
        problems.append("ADR-009 has no accepted Amendment 5 with Martins's Decision and the lock")
    if not oot.has_marker(expectations, oot.EXPECTATIONS_FINAL):
        problems.append(f"{EXPECTATIONS} is not marked final by Martins ('{oot.EXPECTATIONS_FINAL}')")
    return problems


def downturn_passed(check: Any | None, again: Any | None, runs: dict[str, str]) -> bool:
    """Guard 5: the newest downturn check used these runs and passed, itself or under Amendment 5."""
    if check is None or json.loads(check.data.params.get("runs", "{}")) != runs:
        return False
    if check.data.metrics.get("passes") == 1.0:
        return True
    return bool(again is not None and again.data.tags.get("source_run") == check.info.run_id
                and again.data.metrics.get("passes") == 1.0)  # fmt: skip


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def cli(*args: str) -> Any:
    out = subprocess.run(["databricks", *args, "--profile", PROFILE, "-o", "json"], capture_output=True,
                         text=True, check=True).stdout  # fmt: skip
    return json.loads(out) if out.strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check-only", action="store_true", help="run every guard and start nothing")
    args = parser.parse_args(argv)
    problems = document_problems(ADR.read_text(encoding="utf-8"), EXPECTATIONS.read_text(encoding="utf-8"))
    if git("status", "--porcelain", "--untracked-files=no"):
        problems.append("uncommitted changes to tracked files: commit first, so the holdout names exact code")
    import mlflow

    from scripts.register_v2 import newest_amendment5, newest_check, v2_name

    tracking.configure(mlflow)
    client = mlflow.MlflowClient()
    runs: dict[str, str] = {}
    v1_runs: dict[str, str] = {}
    for step in oot.STEPS:
        try:
            runs[step] = str(client.get_model_version_by_alias(environment.registered_model(v2_name(step)),
                                                               environment.CHALLENGER).run_id)  # fmt: skip
        except Exception:  # noqa: BLE001 - not registered
            problems.append(f"no v2 @challenger for step {step}")
        try:  # v1's retired model (version 1), information only
            v1_runs[step] = str(
                client.get_model_version(environment.registered_model(oot.registered_name(step)), "1").run_id
            )
        except Exception:  # noqa: BLE001
            pass
    check, again = newest_check(mlflow), newest_amendment5(mlflow)
    if len(runs) == len(oot.STEPS) and not downturn_passed(check, again, runs):
        problems.append("the downturn check did not pass for the @challenger runs")
    if mlflow.search_runs(
        filter_string="tags.stage = 'v2_holdout'", search_all_experiments=True, output_format="list"
    ):
        problems.append("the v2 holdout has already run: it runs once")
    try:
        scheduling.refuse_blocked_window()  # the Databricks workspace is shared with Bank Health
    except scheduling.BlockedWindow as e:
        problems.append(str(e))
    if problems:
        print("the v2 holdout can't start:\n- " + "\n- ".join(problems))
        return 1
    downturn = check.info.run_id + (f",{again.info.run_id}" if again is not None else "")
    params = {"runs": json.dumps(runs), "v1_runs": json.dumps(v1_runs), "paths": "8",
              "adr_009_commit": git("log", "-1", "--format=%H", "--", str(ADR)),
              "expectations_commit": git("log", "-1", "--format=%H", "--", str(EXPECTATIONS)),
              "downturn_runs": downturn, "code_version": git("rev-parse", "HEAD")}  # fmt: skip
    if args.check_only:
        print("every guard passes; the holdout would start with:\n" + json.dumps(params, indent=1))
        return 0
    from scripts.run_job import deploy

    deploy()
    jobs = cli("jobs", "list")
    job_id = next(j["job_id"] for j in jobs if j["settings"]["name"] == JOB)
    out = cli(
        "jobs", "run-now", "--json", json.dumps({"job_id": job_id, "job_parameters": params}), "--no-wait"
    )
    run_id = out["run_id"]
    print(f"v2 holdout started: run {run_id}; v2 {runs}; code {params['code_version'][:12]}", flush=True)
    while True:
        state = cli("jobs", "get-run", str(run_id))["state"]
        if state.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        time.sleep(30)
    print(f"finished: {state.get('result_state')} {state.get('state_message', '')}")
    task = cli("jobs", "get-run", str(run_id))["tasks"][0]["run_id"]
    output = cli("jobs", "get-run-output", str(task))
    print(output.get("notebook_output", {}).get("result", output.get("error", "")))
    return 0 if state.get("result_state") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
