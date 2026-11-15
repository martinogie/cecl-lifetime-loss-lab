"""Disk guard: check there is room before writing anything large (ADR-001).

The development PC has limited free space (about 13 to 18 GB in November 2026), and the Freddie
sample files would take 8.8 GB unzipped. So every step that writes data first estimates its output
and calls `check_room`. If writing it would leave less than the reserve (3 GB by default), the step
stops before writing anything, instead of filling the drive.
"""

from __future__ import annotations

import shutil
from pathlib import Path

GB = 1024**3
RESERVE_BYTES = 3 * GB  # always keep at least this much free


class NotEnoughDisk(RuntimeError):
    """Writing the planned output would leave less free space than the reserve."""


def free_bytes(path: Path) -> int:
    """Free space on the drive that holds `path` (the nearest existing parent is used)."""
    probe = Path(path).resolve()
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def check_room(path: Path, needed_bytes: int, reserve_bytes: int = RESERVE_BYTES) -> int:
    """Raise NotEnoughDisk unless `needed_bytes` fit with `reserve_bytes` to spare. Returns free bytes."""
    if needed_bytes < 0:
        raise ValueError("needed_bytes can't be negative")
    free = free_bytes(path)
    if free - needed_bytes < reserve_bytes:
        raise NotEnoughDisk(
            f"not enough disk at {path}: {free / GB:.1f} GB free, this step needs about "
            f"{needed_bytes / GB:.1f} GB and {reserve_bytes / GB:.0f} GB must stay free. Free up space first."
        )
    return free


def describe(path: Path) -> str:
    return f"{free_bytes(path) / GB:.1f} GB free at {Path(path).resolve().anchor or path}"
