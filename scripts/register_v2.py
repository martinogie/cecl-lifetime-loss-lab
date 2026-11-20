"""Register model v2 as @challenger and retire v1's alias (ADR-009 Amendment 4, item 3).

    CECL_ENV=dev python -m scripts.register_v2 [--yes]

Refuses unless the newest `v2-downturn-check` run passed (all of tests 1-7), or, under ADR-009 Amendment 5,
the newest `v2-downturn-check-amendment5` re-evaluation of that same run passed (test 7 in percentage
points; `scripts/reevaluate_downturn_v2.py`). Then, per step:
1. registers the final v2 fit, `runs:/<run>/model` (the runs the check used), as
   `<catalog>.ml.pd_v2_transition_<step>` with the alias @challenger, tagged with the check run;
2. removes @challenger from v1's `<catalog>.ml.pd_transition_<step>`. v1's model versions, runs and its
   out-of-time failure (L-032) stay; only the alias goes, and each v1 version keeps a tag saying when and why.

Without `--yes` it only prints what it would do. `@champion` is never touched. API calls, no compute.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cecl import environment, tracking
from cecl.models import oot


def v2_name(step: str) -> str:
    if step not in oot.STEPS:
        raise ValueError(f"unknown step {step!r}")
    return f"pd_v2_transition_{step}"


def newest_check(mlflow: Any) -> Any:
    runs = mlflow.search_runs(filter_string="tags.stage = 'downturn_check' and tags.model_version = 'v2'",
                              order_by=["start_time DESC"], search_all_experiments=True, output_format="list")  # fmt: skip
    if not runs:
        raise SystemExit("no v2 downturn-check run")
    return runs[0]


def newest_amendment5(mlflow: Any) -> Any | None:
    runs = mlflow.search_runs(filter_string="tags.stage = 'downturn_check_amendment5' and tags.model_version = 'v2'",
                              order_by=["start_time DESC"], search_all_experiments=True, output_format="list")  # fmt: skip
    return runs[0] if runs else None


def alias_run(client: Any, name: str, alias: str) -> tuple[str, str] | None:
    try:
        v = client.get_model_version_by_alias(name, alias)
        return str(v.version), str(v.run_id)
    except Exception:  # noqa: BLE001 - no model or no alias
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", action="store_true", help="make the changes (otherwise only print them)")
    args = parser.parse_args(argv)
    import mlflow

    tracking.configure(mlflow)
    check = newest_check(mlflow)
    evidence = check
    if check.data.metrics.get("passes") != 1.0:
        again = newest_amendment5(mlflow)
        if (
            again is None
            or again.data.tags.get("source_run") != check.info.run_id
            or again.data.metrics.get("passes") != 1.0
        ):
            raise SystemExit(f"downturn check {check.info.run_id[:8]} did not pass (nor its Amendment 5 "
                             "re-evaluation): v2 is not registered")  # fmt: skip
        evidence = again
    runs = json.loads(check.data.params["runs"])
    client = mlflow.MlflowClient()
    for step in oot.STEPS:
        new, old = (
            environment.registered_model(v2_name(step)),
            environment.registered_model(oot.registered_name(step)),
        )
        environment.assert_can_set_alias(new, environment.CHALLENGER)
        current = alias_run(client, new, environment.CHALLENGER)
        v1 = alias_run(client, old, environment.CHALLENGER)
        print(f"{step}: register run {runs[step][:8]} as {new}@{environment.CHALLENGER}"
              f"{' (already)' if current and current[1] == runs[step] else ''}; "
              f"retire {old}@{environment.CHALLENGER} (version {v1[0] if v1 else '-'})")  # fmt: skip
        if not args.yes:
            continue
        if not (current and current[1] == runs[step]):
            version = mlflow.register_model(f"runs:/{runs[step]}/model", new)
            client.set_registered_model_alias(new, environment.CHALLENGER, version.version)
            tags = {"model_version": "v2", "step": step, "downturn_check_run": check.info.run_id, "verdict_run": evidence.info.run_id,
                    "rules": evidence.data.tags.get("rules", ""), "holdout": "not yet run"}  # fmt: skip
            for k, v in tags.items():
                client.set_model_version_tag(new, version.version, k, v)
        if v1:
            client.set_model_version_tag(old, v1[0], "challenger_retired",
                                         f"replaced by model v2 after its downturn check {check.info.run_id[:8]}")  # fmt: skip
            client.delete_registered_model_alias(old, environment.CHALLENGER)
    if not args.yes:
        print("dry run: nothing changed (add --yes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
