"""Check the PC can log to the dev MLflow experiment (Phase 2 step 1).

Runs the training preflight (reported, not enforced: this isn't a training run), points MLflow at
the environment's experiment through the Databricks CLI profile, and logs one small run tagged
`connection_check` with the standard tags (environment, git commit, data snapshot) and the
preflight figures. Prints the experiment name and the run id only.

    CECL_ENV=dev python -m scripts.mlflow_check
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from cecl import tracking
from ingestion import ml_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args(argv)

    import mlflow

    pre = ml_preflight.check(args.root)
    print("preflight:", json.dumps(pre.as_dict()))
    path = tracking.configure(mlflow)
    with mlflow.start_run(run_name="connection-check") as run:
        mlflow.set_tags(tracking.standard_tags(args.root) | {"purpose": "connection_check", "phase": "2"})
        mlflow.log_params(
            {"free_memory_gb": round(pre.free_memory_gb, 2), "free_disk_gb": round(pre.free_disk_gb, 2)}
        )
        mlflow.log_metric("preflight_ok", int(pre.ok))
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "connection_check.json"
            note.write_text(json.dumps({"preflight": pre.as_dict()}, indent=1), encoding="utf-8")
            mlflow.log_artifact(str(note))
        print(f"logged run {run.info.run_id} to experiment .../{path.rsplit('/', 1)[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
