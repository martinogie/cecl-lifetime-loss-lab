"""Start the out-of-time test, once (Phase 2 step 7, ADR-009 E).

    CECL_ENV=dev python -m scripts.run_oot_test

Refuses unless: ADR-009 is accepted and committed (no uncommitted changes to tracked files), a
`selection` run exists, and the time is outside the blocked windows. Then starts the Databricks
`oot_test` job with the chosen family, every family's validation run, the COVID sensitivity run and
the ADR-009 commit. The job itself refuses to run a second time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cecl import scheduling, tracking

ADR = Path("docs/adr/009-model-selection-rule.md")
JOB = "cecl_lab_oot_test_dev"
PROFILE = "cecl"


def adr_accepted(text: str) -> bool:
    """Accepted with Martins's Decision, and no amendment still pending (an amendment must be locked
    in before the test, like the original rule)."""
    status = next((line for line in text.splitlines() if line.startswith("- **Status:**")), "")
    pending = any("Status of this amendment:** proposed" in line for line in text.splitlines())
    return "accepted" in status and "**Decision (Martins" in text and not pending


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    if not adr_accepted(ADR.read_text(encoding="utf-8")):
        raise SystemExit("ADR-009 is not accepted with Martins's Decision: the test can't run (ADR-009 E)")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("uncommitted changes to tracked files: commit first, so the test names exact code")
    adr_commit = git("log", "-1", "--format=%H", "--", str(ADR))
    scheduling.refuse_blocked_window()  # the Databricks workspace is shared with Bank Health
    import mlflow

    tracking.configure(mlflow)
    selections = mlflow.search_runs(filter_string="tags.stage = 'selection'", order_by=["start_time DESC"],
                                    output_format="list")  # fmt: skip
    if not selections:
        raise SystemExit("no selection run: run scripts/select_model.py first")
    sel = selections[0]
    runs = {k.removeprefix("run_"): v for k, v in sel.data.params.items() if k.startswith("run_")}
    sens = mlflow.search_runs(filter_string="tags.stage = 'validation' and tags.sensitivity = 'covid_in_training'",
                              order_by=["start_time DESC"], output_format="list")  # fmt: skip
    if sens:
        runs["sensitivity_covid"] = sens[0].info.run_id
    params = {"runs": json.dumps(runs), "chosen": sel.data.tags["chosen"], "adr_009_commit": adr_commit,
              "code_version": git("rev-parse", "HEAD")}  # fmt: skip
    jobs = json.loads(subprocess.run(["databricks", "jobs", "list", "--profile", PROFILE, "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)  # fmt: skip
    job_id = next(j["job_id"] for j in jobs if j["settings"]["name"] == JOB)
    body = json.dumps({"job_id": job_id, "job_parameters": params})
    out = subprocess.run(["databricks", "jobs", "run-now", "--json", body, "--no-wait", "--profile", PROFILE,
                          "-o", "json"], capture_output=True, text=True, check=True).stdout  # fmt: skip
    print(
        f"out-of-time test started: run {json.loads(out).get('run_id')}; chosen {params['chosen']}; "
        f"families {sorted(runs)}; ADR-009 commit {adr_commit[:12]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
