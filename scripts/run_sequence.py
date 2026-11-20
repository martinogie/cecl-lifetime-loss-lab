"""Run a planned list of Databricks jobs back to back (Phase 2 plan, multi-state: no daily cap).

    CECL_ENV=local python -m scripts.run_sequence --plan transitions            # the 9 models
    CECL_ENV=local python -m scripts.run_sequence --plan transitions --from 4   # resume at the 4th

Each job goes through scripts/run_job.py (clean-commit guard, deploy from that commit, the commit
passed as `code_version`, minutes written to the run log). Before each start it waits out a blocked
window (Monday before 13:00 Chicago, 02:30-04:00 every day; cecl/scheduling.py) instead of failing.
It stops at the first job that does not succeed (a failure, or a Databricks cut-off), so nothing
runs on top of a problem; `--from` resumes the remaining jobs the next day.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from cecl import environment, scheduling
from scripts import run_job

TRAIN = "cecl_lab_train_transition_dev"
# Shortest first, so a cut-off hits the longest (EBM on T-current) last.
PLANS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "transitions": [
        (TRAIN, {"start_state": start, "family": family} | extra)
        for start in ("60", "30", "current")
        for family, extra in (
            ("logistic", {}),
            ("lightgbm", {}),
            # T-current's EBM: 1 in 4 of the rows that stay current or prepay, re-weighted (recorded)
            (
                "ebm",
                {"ebm_sample_every": "4", "ebm_sample_states": "stay;prepaid"} if start == "current" else {},
            ),
        )
    ],
}
WAIT_SECONDS = 60


def wait_for_window(now: datetime | None = None, sleep: object = time.sleep) -> int:
    """Wait (one minute at a time) while starting would fall in a blocked window; returns minutes waited."""
    waited = 0
    while scheduling.blocked_reason(now or datetime.now(scheduling.TZ)):
        if waited == 0:
            print(f"waiting: {scheduling.blocked_reason(datetime.now(scheduling.TZ))}", flush=True)
        sleep(WAIT_SECONDS)  # type: ignore[operator]
        waited += 1
        now = None
    return waited


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--plan", choices=sorted(PLANS), required=True)
    parser.add_argument("--from", dest="first", type=int, default=1, help="1-based position to start at")
    parser.add_argument(
        "--to", dest="last", type=int, default=0, help="1-based position to stop after (0: all)"
    )
    args = parser.parse_args(argv)
    environment.current_env()
    plan = PLANS[args.plan]
    last = args.last or len(plan)
    for i, (job, params) in enumerate(plan[args.first - 1 : last], start=args.first):
        wait_for_window()
        print(f"[{i}/{len(plan)}] {job} {params}", flush=True)
        code = run_job.main(["--job", job, *[a for k, v in params.items() for a in ("--param", f"{k}={v}")]])
        if code != 0:
            print(
                f"stopped at job {i}: it did not succeed (resume with --from {i} after checking why)",
                flush=True,
            )
            return code
    print("all jobs succeeded", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
