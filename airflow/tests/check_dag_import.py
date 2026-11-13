"""DAG import check, run INSIDE the Airflow image (CI job `dag-import`). Never run on Windows.

Loads airflow/dags exactly as Airflow does (DagBag), in fresh processes:
    CECL_ENV=prod    must import with no errors; every DAG tagged prod
    CECL_ENV=dev     must import with no errors; every DAG tagged dev
    CECL_ENV unset   must FAIL to import, naming CECL_ENV (no default)
    CECL_ENV=local   must FAIL to import (Airflow runs only as dev or prod)
and for every DAG: its schedule may never fire on a Monday before 13:00 Chicago or between 02:30 and
04:00 any day (cecl/scheduling.py).
Needs no secrets: nothing connects while a DAG is parsed.

    docker run --rm -v "$PWD:/repo" -w /repo cecl-airflow:ci python airflow/tests/check_dag_import.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

DAGS = os.environ.get("DAGS_FOLDER", "/opt/airflow/dags")

PARSE = r"""
import json, logging, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
from airflow.dag_processing.dagbag import DagBag
bag = DagBag(DAGS)
out = {"dag_ids": sorted(bag.dags), "import_errors": {k: str(v)[-600:] for k, v in bag.import_errors.items()}, "dags": {}}
for dag_id, dag in bag.dags.items():
    schedule = getattr(dag, "schedule", None)
    if not isinstance(schedule, str) and schedule is not None:
        schedule = getattr(dag.timetable, "summary", repr(schedule))
    out["dags"][dag_id] = {"schedule": schedule, "tags": sorted(dag.tags)}
print("RESULT=" + json.dumps(out))
""".replace("DAGS", repr(DAGS))


def parse(env: str | None) -> dict:
    # Airflow 3's DagBag has no include_examples argument; example DAGs are switched off by setting.
    child = {**os.environ, "AIRFLOW__CORE__LOAD_EXAMPLES": "False"}
    child.pop("CECL_ENV", None)
    if env is not None:
        child["CECL_ENV"] = env
    proc = subprocess.run(
        [sys.executable, "-c", PARSE], env=child, capture_output=True, text=True, timeout=600
    )
    line = next((x for x in proc.stdout.splitlines() if x.startswith("RESULT=")), None)
    if line is None:
        raise SystemExit(f"DagBag did not finish for CECL_ENV={env}:\n{proc.stderr[-3000:]}")
    return json.loads(line.removeprefix("RESULT="))


def main() -> int:
    from cecl.scheduling import schedule_problems

    problems: list[str] = []
    for env in ("prod", "dev"):
        result = parse(env)
        if result["import_errors"]:
            problems.append(f"{env}: import errors {result['import_errors']}")
        if not result["dag_ids"]:
            problems.append(f"{env}: no DAGs found in {DAGS}")
        for dag_id, info in result["dags"].items():
            if env not in info["tags"]:
                problems.append(f"{env}: {dag_id} is not tagged {env}")
            schedule = info["schedule"]
            if schedule in (None, "None", "@once"):
                continue
            try:
                problems.extend(f"{dag_id}: {p}" for p in schedule_problems(schedule))
            except ValueError:
                problems.append(
                    f"{dag_id}: schedule {schedule!r} is not a cron expression; can't prove it avoids the blocked windows"
                )
        print(f"CECL_ENV={env}: {len(result['dag_ids'])} DAG(s) {result['dag_ids']}, schedules ok")

    for env in (None, "local"):
        result = parse(env)
        errors = " ".join(result["import_errors"].values())
        if result["dag_ids"] or "CECL_ENV" not in errors:
            problems.append(f"CECL_ENV={env}: DAGs must fail to import, naming CECL_ENV")
        else:
            print(f"CECL_ENV={env}: refused, as required")

    for p in problems:
        print("PROBLEM", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
