"""Training preflight on the PC (Phase 2 plan): refuse to start a run that would starve the machine.

The PC has 2 cores, 7.7 GB of memory and a disk that is often nearly full, so before every training
run:
- the dev Airflow stack must be stopped (its containers use about 1 GB of memory and some CPU);
- free memory must be at least MIN_FREE_MEMORY_GB;
- free disk must be at least MIN_FREE_DISK_GB (the loaders' 3 GB reserve plus room for the run).
Any failure raises with every problem listed. The figures go into the run log and MLflow, together
with the run's peak memory (`peak_memory_gb`, read at the end of the run).

The measuring functions can be passed in, so the rules are tested without psutil or Docker.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingestion import disk

MIN_FREE_MEMORY_GB = 4.0
MIN_FREE_DISK_GB = 5.0
AIRFLOW_PREFIX = "cecl-dev-airflow-"
GB = 1024**3


class PreflightFailed(RuntimeError):
    """The machine isn't ready for a training run; nothing was started."""


def free_memory_gb() -> float:  # pragma: no cover - thin wrapper over psutil
    import psutil

    return float(psutil.virtual_memory().available) / GB


def peak_memory_gb() -> float:  # pragma: no cover - thin wrapper over psutil
    """This process's peak memory so far (peak working set on Windows, max RSS elsewhere)."""
    import psutil

    info = psutil.Process().memory_info()
    return float(getattr(info, "peak_wset", info.rss)) / GB


def running_airflow_containers() -> list[str]:
    """Names of running dev Airflow containers ([] if Docker isn't running at all)."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [n for n in out.split() if n.startswith(AIRFLOW_PREFIX)]


@dataclass
class Preflight:
    free_memory_gb: float
    free_disk_gb: float
    airflow_running: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {"free_memory_gb": round(self.free_memory_gb, 2), "free_disk_gb": round(self.free_disk_gb, 2),
                "airflow_running": self.airflow_running, "problems": self.problems}  # fmt: skip


def check(
    root: Path,
    memory: Callable[[], float] = free_memory_gb,
    containers: Callable[[], list[str]] = running_airflow_containers,
    disk_free: Callable[[Path], int] = disk.free_bytes,
    min_memory_gb: float = MIN_FREE_MEMORY_GB,
    min_disk_gb: float = MIN_FREE_DISK_GB,
) -> Preflight:
    result = Preflight(memory(), disk_free(Path(root)) / GB, containers())
    if result.airflow_running:
        result.problems.append(
            f"dev Airflow is running ({len(result.airflow_running)} containers): stop it first "
            "(cd airflow; docker compose -f docker-compose.yaml -f docker-compose.dev.yaml down)"
        )
    if result.free_memory_gb < min_memory_gb:
        result.problems.append(f"free memory {result.free_memory_gb:.1f} GB, need {min_memory_gb:.1f} GB")
    if result.free_disk_gb < min_disk_gb:
        result.problems.append(f"free disk {result.free_disk_gb:.1f} GB, need {min_disk_gb:.1f} GB")
    return result


def enforce(result: Preflight) -> Preflight:
    if not result.ok:
        raise PreflightFailed("training preflight failed:\n  " + "\n  ".join(result.problems))
    return result
