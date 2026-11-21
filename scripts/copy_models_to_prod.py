"""Copy the v2 transition models from the dev registry into the prod registry (VM cutover, once).

    CECL_ENV=prod python -m scripts.copy_models_to_prod [--yes]

For each step it reads the dev version that carries @challenger (`cecl_lab_dev.ml.pd_v2_transition_<step>`),
registers the same training run (`runs:/<run>/model`) as `cecl_lab.ml.pd_v2_transition_<step>`, copies the
dev version's tags, adds `copied_from`, and sets @challenger. It checks the new prod version came from the
same run and finished uploading (status READY).

Why from the PC and not inside the clone job (67_clone_to_prod): Databricks serverless compute is denied
writes to Unity Catalog's model storage on this workspace (S3 "explicit deny"), so model files can only be
uploaded from a client, which is how the dev versions were registered too (scripts/register_v2.py).

Without `--yes` it only prints what it would do. A prod version already at @challenger from the same run
is left alone (safe to re-run). @champion is never touched. API calls, no compute.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from cecl import environment, tracking
from cecl.models import oot

SOURCE_CATALOG = "cecl_lab_dev"


def names(step: str, target_catalog: str) -> tuple[str, str]:
    """(dev name, prod name) of one step's v2 model."""
    if step not in oot.STEPS:
        raise ValueError(f"unknown step {step!r}")
    model = f"pd_v2_transition_{step}"
    if target_catalog == SOURCE_CATALOG:
        raise ValueError("source and target catalogs are the same")
    return f"{SOURCE_CATALOG}.ml.{model}", f"{target_catalog}.ml.{model}"


def copy_tags(src_tags: dict[str, str], src_name: str, src_version: str) -> dict[str, str]:
    """Tags for the prod version: the dev version's own, plus where it came from."""
    return {**src_tags, "copied_from": f"{src_name} v{src_version}"}


def wait_ready(client: Any, name: str, version: str, seconds: int = 300) -> str:
    status = ""
    for _ in range(seconds // 5):
        status = str(client.get_model_version(name, version).status)
        if status.endswith("READY") and not status.endswith("NOT_READY"):
            return status
        if "FAILED" in status:
            break
        time.sleep(5)
    raise RuntimeError(f"{name} v{version} is {status}, not READY")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", action="store_true", help="make the changes (otherwise only print them)")
    args = parser.parse_args(argv)
    if environment.current_env() != "prod":
        raise SystemExit("copy_models_to_prod runs only with CECL_ENV=prod")
    target = environment.databricks_schema("ml").split(".")[0]
    import mlflow

    tracking_uri, registry_uri = tracking.tracking_uris()
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(registry_uri)
    client = mlflow.MlflowClient()
    for step in oot.STEPS:
        src_name, dst_name = names(step, target)
        environment.assert_can_set_alias(dst_name, environment.CHALLENGER)
        src = client.get_model_version_by_alias(src_name, environment.CHALLENGER)
        run = str(src.run_id or "")
        if not run:
            raise RuntimeError(f"{src_name} v{src.version} has no training run to copy from")
        try:
            cur = client.get_model_version_by_alias(dst_name, environment.CHALLENGER)
        except Exception:  # noqa: BLE001 - no prod model or no alias yet
            cur = None
        if cur is not None and cur.run_id == run:
            print(f"{step}: {dst_name} v{cur.version}@{environment.CHALLENGER} already from run {run[:8]}")
            continue
        print(f"{step}: register run {run[:8]} ({src_name} v{src.version}) as {dst_name}@challenger")
        if not args.yes:
            continue
        new = mlflow.register_model(f"runs:/{run}/model", dst_name)
        if new.run_id != run:
            raise RuntimeError(f"{dst_name} v{new.version} is not from run {run}")
        status = wait_ready(client, dst_name, str(new.version))
        for k, v in copy_tags(dict(src.tags or {}), src_name, str(src.version)).items():
            client.set_model_version_tag(dst_name, new.version, k, v)
        client.set_registered_model_alias(dst_name, environment.CHALLENGER, new.version)
        print(f"  -> {dst_name} v{new.version} {status}, @challenger set, {len(src.tags or {}) + 1} tags")
    if not args.yes:
        print("dry run: nothing changed (add --yes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
