"""MLflow tracking for the models (Phase 2): where runs go and what every run is tagged with.

Runs are logged to the environment's experiment in the Databricks workspace and models are
registered in Unity Catalog (`<catalog>.ml.<model>`), reached through a Databricks CLI profile
(`cecl` by default; Martins's login, so no token is ever read by this code). Training runs on the
PC as CECL_ENV=dev: the PC is where dev runs, the workspace is where dev results live
(`cecl/environment.py`). `local` and `ci` have no experiment and refuse.

Every run carries the same tags, so any result can be traced to its code and data:
environment, git commit, and the quarter-end data snapshot (name and fingerprint) it was trained on.

mlflow and databricks-sdk are imported only when used: they are in requirements-ml.txt, not in the
light CI environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from cecl import environment

PROFILE_VAR = "CECL_DATABRICKS_PROFILE"
DEFAULT_PROFILE = "cecl"
SNAPSHOT_VAR = "CECL_DATA_SNAPSHOT"
DEFAULT_SNAPSHOT = "cecl-2026Q2-data-v1"


def profile() -> str:
    return os.environ.get(PROFILE_VAR, "").strip() or DEFAULT_PROFILE


def tracking_uris(name: str | None = None) -> tuple[str, str]:
    """(tracking URI, registry URI) for a Databricks CLI profile: runs in the workspace, models in
    Unity Catalog."""
    p = name or profile()
    return f"databricks://{p}", f"databricks-uc://{p}"


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip() + ("+uncommitted" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def snapshot_tags(root: Path, name: str | None = None) -> dict[str, str]:
    """The data snapshot a run trains on: its name and fingerprint, from the local manifest."""
    snap = name or os.environ.get(SNAPSHOT_VAR, "").strip() or DEFAULT_SNAPSHOT
    manifest = Path(root) / "local" / "snapshots" / snap / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(f"no manifest for data snapshot {snap} at {manifest}")
    fingerprint = json.loads(manifest.read_text(encoding="utf-8"))["fingerprint"]
    return {"data_snapshot": snap, "data_fingerprint": fingerprint}


def standard_tags(root: Path, snapshot: str | None = None) -> dict[str, str]:
    return {"cecl_env": environment.current_env(), "git_commit": git_commit()} | snapshot_tags(root, snapshot)


def configure(mlflow: Any = None, workspace_user: str | None = None) -> str:
    """Point MLflow at this environment's experiment and registry; return the experiment path.

    `mlflow` and `workspace_user` can be passed in (tests); otherwise mlflow is imported and the
    user is read from the workspace through the CLI profile (never printed).
    """
    settings = environment.require_airflow_env()  # dev or prod: local and ci have no experiment
    # Pin every Databricks login in this process to CECL's profile. Without this, parts of MLflow
    # (artifact uploads) fall back to the CLI's *default* profile, which on Martins's PC is Bank
    # Health's: CECL must never act under Bank Health's identity (found in Phase 2 step 1).
    os.environ["DATABRICKS_CONFIG_PROFILE"] = profile()
    # MLflow prints emoji ("View run ..."), which the Windows console's code page can't encode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if mlflow is None:  # pragma: no cover - needs requirements-ml.txt and the workspace
        import mlflow
    if workspace_user is None:  # pragma: no cover - needs the workspace
        from databricks.sdk import WorkspaceClient

        workspace_user = WorkspaceClient(profile=profile()).current_user.me().user_name
    tracking, registry = tracking_uris()
    mlflow.set_tracking_uri(tracking)
    mlflow.set_registry_uri(registry)
    path = environment.mlflow_experiment_path(str(workspace_user))
    mlflow.set_experiment(path)
    assert settings.mlflow_experiment and path.endswith(settings.mlflow_experiment)
    return path
