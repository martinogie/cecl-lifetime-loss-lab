"""The Databricks bundle can't run anything on a timer by accident (step 9 follow-up).

The dev target no longer uses "development mode" (it prefixed every name with the username), and
development mode was also what paused schedules in dev. So the bundle itself must guarantee it: no
job has a schedule, file-arrival trigger or continuous mode unless it is explicitly paused. Jobs run
by hand or from Airflow, which keeps the blocked windows (cecl/scheduling.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BUNDLE = Path(__file__).resolve().parents[2] / "databricks" / "databricks.yml"
TIMERS = ("schedule", "trigger", "continuous")


def bundle() -> dict[str, Any]:
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))


def all_jobs(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Jobs at the top level and in every target (a target can add or override jobs)."""
    jobs = dict(doc.get("resources", {}).get("jobs", {}))
    for name, target in (doc.get("targets") or {}).items():
        for key, job in ((target or {}).get("resources", {}).get("jobs", {}) or {}).items():
            jobs[f"{name}:{key}"] = job
    return jobs


def timers_left_running(jobs: dict[str, dict[str, Any]]) -> list[str]:
    found = []
    for name, job in jobs.items():
        for timer in TIMERS:
            setting = (job or {}).get(timer)
            if setting is not None and str(setting.get("pause_status", "")).upper() != "PAUSED":
                found.append(f"{name}.{timer}")
    return found


def test_bundle_has_jobs_and_none_runs_on_a_timer():
    jobs = all_jobs(bundle())
    assert jobs, "no jobs found in databricks.yml; update this test"
    assert timers_left_running(jobs) == [], "pause it, or let Airflow trigger the job"


def test_the_check_itself_catches_a_timer():
    jobs = {
        "a": {"schedule": {"quartz_cron_expression": "0 0 7 ? * MON"}},
        "b": {"trigger": {"pause_status": "PAUSED"}},
        "c": {"continuous": {"pause_status": "UNPAUSED"}},
    }
    assert timers_left_running(jobs) == ["a.schedule", "c.continuous"]


def test_dev_target_does_not_use_development_mode():
    # Development mode prefixes every schema, volume and model with dev_<username>_ (step 9).
    assert bundle()["targets"]["dev"].get("mode") != "development"
