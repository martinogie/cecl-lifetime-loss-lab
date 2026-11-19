"""Start the out-of-time test, once (ADR-009 E; multi-state, Amendments 1 E and 2).

    CECL_ENV=dev python -m scripts.run_oot_test --check-only   # every guard, starts nothing
    CECL_ENV=dev python -m scripts.run_oot_test                # guards, deploy, start the job

Refuses unless every guard passes:
1. ADR-009 is accepted with Martins's Decision and no amendment is pending;
2. no uncommitted changes to tracked files (the test names exact code);
3. Martins's written expectation is marked final (docs/phase2/out_of_time_expectations.md) and the
   pre-registered result statement is marked approved (docs/phase2/out_of_time_result_statement.md),
   both by status lines he sets himself;
4. a multi-state `selection` run names the locked set (scripts/lock_model_set.py);
5. each step's @challenger in Unity Catalog points to the locked run (scripts/register_challenger.py);
6. the COVID sensitivity has been run for the locked family at all three steps, at the locked commit;
7. the time is outside the blocked windows (never Monday before 13:00 Chicago, never 02:30-04:00).
Then it deploys the bundle from the clean commit and starts the Databricks `oot_test` job. The job
itself refuses to run a second time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from cecl import environment, scheduling, tracking
from cecl.models import oot

ADR = Path("docs/adr/009-model-selection-rule.md")
EXPECTATIONS = Path("docs/phase2/out_of_time_expectations.md")
STATEMENT = Path("docs/phase2/out_of_time_result_statement.md")
JOB = "cecl_lab_oot_test_dev"
PROFILE = "cecl"
DESIGN = "multi-state (ADR-011)"


def adr_accepted(text: str) -> bool:
    """Accepted with Martins's Decision, and no amendment still pending (an amendment must be locked
    in before the test, like the original rule)."""
    status = next((line for line in text.splitlines() if line.startswith("- **Status:**")), "")
    pending = any("Status of this amendment:** proposed" in line for line in text.splitlines())
    return "accepted" in status and "**Decision (Martins" in text and not pending


def document_problems(adr: str, expectations: str, statement: str) -> list[str]:
    """Guards 1 and 3, on the documents' text."""
    problems = []
    if not adr_accepted(adr):
        problems.append("ADR-009 is not accepted with Martins's Decision (or an amendment is pending)")
    if not oot.has_marker(expectations, oot.EXPECTATIONS_FINAL):
        problems.append(f"{EXPECTATIONS} is not marked final by Martins ('{oot.EXPECTATIONS_FINAL}')")
    if not oot.has_marker(statement, oot.STATEMENT_APPROVED):
        problems.append(f"{STATEMENT} is not marked approved by Martins ('{oot.STATEMENT_APPROVED}')")
    return problems


def info_from_selection(
    params: dict[str, str], locked: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """For information in the test: every other model at each step, and the combination with the
    current step's LightGBM swapped in (the 2017-2019 comparison)."""
    models: dict[str, dict[str, str]] = {}
    for key, run in params.items():
        if key.startswith("info_"):
            step, family = key.removeprefix("info_").split("_", 1)
            if run != locked.get(step):
                models.setdefault(step, {})[family] = run
    combos = {}
    lgbm = models.get("current", {}).get("lightgbm")
    if lgbm:
        combos["info_lightgbm_current"] = {**locked, "current": lgbm}
    return combos, models


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check-only", action="store_true", help="run every guard and start nothing")
    args = parser.parse_args(argv)
    problems = document_problems(*(p.read_text(encoding="utf-8") for p in (ADR, EXPECTATIONS, STATEMENT)))
    if git("status", "--porcelain", "--untracked-files=no"):
        problems.append("uncommitted changes to tracked files: commit first, so the test names exact code")
    import mlflow

    tracking.configure(mlflow)
    sel = mlflow.search_runs(filter_string=f"tags.stage = 'selection' and tags.design = '{DESIGN}'",
                             order_by=["start_time DESC"], search_all_experiments=True, output_format="list")  # fmt: skip
    locked: dict[str, str] = {}
    if not sel:
        problems.append("no multi-state selection run: run scripts/lock_model_set.py")
    else:
        locked = oot.locked_set(sel[0].data.params)
        client = mlflow.MlflowClient()
        registered: dict[str, str | None] = {}
        for step in oot.STEPS:
            try:
                v = client.get_model_version_by_alias(environment.registered_model(oot.registered_name(step)),
                                                      environment.CHALLENGER)  # fmt: skip
                registered[step] = str(v.run_id)
            except Exception:  # noqa: BLE001 - not registered yet
                registered[step] = None
        bad = oot.challenger_mismatches(locked, registered)
        if bad:
            problems.append(
                f"@challenger missing or not the locked run for step(s) {bad}: run scripts/register_challenger.py"
            )
        family = sel[0].data.tags.get("chosen", "").split(";")[0].split(":")[-1] or "logistic"
        commit = sel[0].data.tags.get("locked_commit", "")
        covid = mlflow.search_runs(filter_string=f"tags.sensitivity = 'covid_in_training' and tags.git_commit = '{commit}'",
                                   search_all_experiments=True, output_format="list")  # fmt: skip
        missing = oot.covid_sensitivity_missing([dict(r.data.tags) for r in covid], family)
        if missing:
            problems.append(f"COVID sensitivity not run at {commit[:12]} for {family}, step(s) {missing}")
    try:
        scheduling.refuse_blocked_window()  # the Databricks workspace is shared with Bank Health
    except scheduling.BlockedWindow as e:
        problems.append(str(e))
    if problems:
        print("the out-of-time test can't start:\n- " + "\n- ".join(problems))
        return 1
    combos, models = info_from_selection(sel[0].data.params, locked)
    params = {"locked": json.dumps(locked), "info_combinations": json.dumps(combos), "info_models": json.dumps(models),
              "selection_run": sel[0].info.run_id, "adr_009_commit": git("log", "-1", "--format=%H", "--", str(ADR)),
              "code_version": git("rev-parse", "HEAD")}  # fmt: skip
    if args.check_only:
        print("every guard passes; the test would start with:\n" + json.dumps(params, indent=1))
        return 0
    from scripts.run_job import deploy

    deploy()
    jobs = json.loads(subprocess.run(["databricks", "jobs", "list", "--profile", PROFILE, "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)  # fmt: skip
    job_id = next(j["job_id"] for j in jobs if j["settings"]["name"] == JOB)
    body = json.dumps({"job_id": job_id, "job_parameters": params})
    out = subprocess.run(["databricks", "jobs", "run-now", "--json", body, "--no-wait", "--profile", PROFILE,
                          "-o", "json"], capture_output=True, text=True, check=True).stdout  # fmt: skip
    print(
        f"out-of-time test started: run {json.loads(out).get('run_id')}; locked {locked}; "
        f"selection run {params['selection_run'][:8]}; code {params['code_version'][:12]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
