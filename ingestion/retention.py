"""How long raw economic data downloads are kept (raw/macro/<source>/<date>/).

FHFA revises past quarters in every release, so a raw download is the only proof of which numbers
a run used. The rule:
- keep the newest KEEP_LATEST downloads of each source (about two months of weekly runs);
- keep, forever, every download recorded in a snapshot manifest (local/snapshots/*/manifest.json);
- everything else may be removed.

Freddie Mac's files are never touched by this module (they are downloaded by hand and are the
inputs of every snapshot). Deleting is hard to undo, so `plan` only lists what would go; `apply`
deletes only when called explicitly (`python -m scripts.retention --apply`), never on a schedule.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

KEEP_LATEST = 8


@dataclass
class Plan:
    keep: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    remove_bytes: int = 0


def snapshot_folders(root: Path) -> set[str]:
    """raw/macro/<source>/<date> folders that any snapshot manifest lists a file from."""
    used: set[str] = set()
    for manifest in (Path(root) / "local" / "snapshots").glob("*/manifest.json"):
        for entry in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            parts = entry["path"].split("/")
            if parts[:2] == ["raw", "macro"] and len(parts) >= 5:
                used.add("/".join(parts[:4]))
    return used


def plan(root: Path, keep_latest: int = KEEP_LATEST) -> Plan:
    root = Path(root)
    base = root / "raw" / "macro"
    result = Plan()
    if not base.exists():
        return result
    protected = snapshot_folders(root)
    for source in sorted(p for p in base.iterdir() if p.is_dir()):
        dates = sorted((p for p in source.iterdir() if p.is_dir()), reverse=True)
        for i, folder in enumerate(dates):
            rel = folder.relative_to(root).as_posix()
            if i < keep_latest or rel in protected:
                result.keep.append(rel)
            else:
                result.remove.append(rel)
                result.remove_bytes += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    return result


def apply(root: Path, chosen: Plan) -> list[str]:
    """Delete the folders a plan lists for removal. Only paths under raw/macro are accepted."""
    root = Path(root).resolve()
    removed = []
    for rel in chosen.remove:
        folder = (root / rel).resolve()
        if not folder.is_relative_to(root / "raw" / "macro"):
            raise ValueError(f"refusing to delete outside raw/macro: {rel}")
        shutil.rmtree(folder)
        removed.append(rel)
    return removed
