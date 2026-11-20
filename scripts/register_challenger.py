"""Register the locked combination in Unity Catalog with the alias @challenger (multi-state, ADR-011).

    CECL_ENV=dev python -m scripts.register_challenger

Takes the newest multi-state `selection` run (`scripts/lock_model_set.py`) and registers each step's
locked model, `runs:/<run>/model`, as `<catalog>.ml.pd_transition_<step>` (current, 30, 60) with the
alias @challenger. Martins approved registering @challenger before the out-of-time test (18 November
2026); each version is tagged with the selection run and "out_of_time_test: not yet run".
`@champion` is never set here: only prod moves it, after the test passes and Martins's written review
(ADR-009 Amendment 2 (c); cecl/environment.py assert_can_set_alias). Re-running is safe: a step whose
@challenger already points to the locked run is left alone. API calls, no compute.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from cecl import environment, tracking
from cecl.models import oot

DESIGN = "multi-state (ADR-011)"


def newest_selection(mlflow: Any) -> Any:
    runs = mlflow.search_runs(filter_string=f"tags.stage = 'selection' and tags.design = '{DESIGN}'",
                              order_by=["start_time DESC"], search_all_experiments=True, output_format="list")  # fmt: skip
    if not runs:
        raise SystemExit("no multi-state selection run: run scripts/lock_model_set.py first")
    return runs[0]


def challenger_run(client: Any, name: str) -> str | None:
    """The run behind `name`@challenger, or None if the model or alias does not exist yet."""
    try:
        return str(client.get_model_version_by_alias(name, environment.CHALLENGER).run_id)
    except Exception:  # noqa: BLE001 - no model or no alias yet
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    import mlflow

    tracking.configure(mlflow)
    sel = newest_selection(mlflow)
    locked = oot.locked_set(sel.data.params)
    client = mlflow.MlflowClient()
    for step, run_id in locked.items():
        v2 = environment.registered_model(f"pd_v2_transition_{step}")
        if challenger_run(client, v2) is not None:  # ADR-009 Amendment 4: v1's alias was retired for v2
            raise SystemExit(
                f"{v2}@{environment.CHALLENGER} exists: v1's alias is retired; not re-setting it"
            )
        name = environment.registered_model(oot.registered_name(step))
        environment.assert_can_set_alias(name, environment.CHALLENGER)
        if challenger_run(client, name) == run_id:
            print(f"{name}@{environment.CHALLENGER} already points to run {run_id[:8]}")
            continue
        version = mlflow.register_model(f"runs:/{run_id}/model", name)
        client.set_registered_model_alias(name, environment.CHALLENGER, version.version)
        for k, v in {"selection_run": sel.info.run_id, "step": step, "locked_commit": sel.data.tags.get("locked_commit", ""),
                     "out_of_time_test": "not yet run"}.items():  # fmt: skip
            client.set_model_version_tag(name, version.version, k, v)
        print(f"registered {name} version {version.version} (run {run_id[:8]}) as @{environment.CHALLENGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
