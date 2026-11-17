"""Register the chosen model in Unity Catalog with the alias @challenger (Phase 2 step 8).

    CECL_ENV=dev python -m scripts.register_challenger

Takes the newest `selection` run's chosen family and its validation run, refuses unless the
out-of-time test has run, registers `runs:/<run>/model` as `<catalog>.ml.pd_hazard` and sets
`@challenger`. `@champion` is never set here: only prod moves it, after Martins's review
(cecl/environment.py assert_can_set_alias). API calls, no compute.
"""

from __future__ import annotations

import argparse
import sys

from cecl import environment, tracking

MODEL = "pd_hazard"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    import mlflow

    tracking.configure(mlflow)
    name = environment.registered_model(MODEL)
    environment.assert_can_set_alias(name, environment.CHALLENGER)
    sel = mlflow.search_runs(filter_string="tags.stage = 'selection'", order_by=["start_time DESC"],
                             output_format="list")  # fmt: skip
    test = mlflow.search_runs(filter_string="tags.stage = 'out_of_time_test'", output_format="list")
    if not sel or not test:
        raise SystemExit("needs a selection run and the out-of-time test run first")
    chosen = sel[0].data.tags["chosen"]
    run_id = sel[0].data.params[f"run_{chosen}"]
    version = mlflow.register_model(f"runs:/{run_id}/model", name)
    client = mlflow.MlflowClient()
    client.set_registered_model_alias(name, environment.CHALLENGER, version.version)
    client.set_model_version_tag(name, version.version, "out_of_time_test_run", test[0].info.run_id)
    print(
        f"registered {name} version {version.version} ({chosen}, run {run_id[:8]}) as @{environment.CHALLENGER}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
