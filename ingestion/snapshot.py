"""Quarter-end snapshots (control C07) and their check (control C08), for the local data.

A snapshot is a manifest: every input and output file of the data foundation, with its size, row
count and SHA-256 checksum, plus the code version (git commit and tree). With it, anyone can prove
that a later rebuild produced exactly the same data, file by file.

- Inputs: Freddie's sample zips (raw/freddie/sample) and the economic data downloads the bronze
  tables were built from (raw/macro/<source>/<date>, the latest download of each source).
- Outputs: every Parquet file under local/ (bronze, silver, panel).
- Checksums: the file's bytes. For tables that record when they were downloaded (economic bronze,
  column `downloaded_at`), a second checksum leaves that column out (`content_sha256`), because a
  rebuild from the same raw files has a new download time but must have the same content.
- The code version is the commit and its *tree* (the hash of the files themselves): the tree stays
  the same when a branch is squash-merged, so the snapshot still names the code after the merge.

Phase 1 step 9 adds the Delta table versions, and Phase 2 the MLflow model version; the manifest
gets those fields then. The manifest is written to local/snapshots/<name>/manifest.json, and the
whole manifest also goes into the message of an annotated git tag (`cecl-<quarter>-data-v<n>`),
so the public repository holds the record even if this PC is lost. It contains file names, sizes,
row counts and checksums only: no loan data.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from cecl import environment

VOLATILE_COLUMNS = ("downloaded_at",)  # left out of content_sha256
CHUNK = 8 * 1024 * 1024
NAME = re.compile(r"^cecl-(\d{4})Q([1-4])-data-v(\d+)$")


def snapshot_name(quarter: str, version: int) -> str:
    """'2026Q2', 1 -> 'cecl-2026Q2-data-v1' (also the git tag name)."""
    name = f"cecl-{quarter}-data-v{version}"
    if not NAME.match(name):
        raise ValueError(f"quarter must look like 2026Q2, got {quarter!r}")
    return name


def quarter_end(quarter: str) -> str:
    year, q = int(quarter[:4]), int(quarter[-1])
    return {1: f"{year}-03-31", 2: f"{year}-06-30", 3: f"{year}-09-30", 4: f"{year}-12-31"}[q]


def snapshot_dir(root: Path, name: str) -> Path:
    return Path(root) / "local" / "snapshots" / name


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def content_sha256(table: pa.Table) -> str:
    """Checksum of a table's values (Arrow IPC bytes), leaving out the volatile columns."""
    keep = [c for c in table.column_names if c not in VOLATILE_COLUMNS]
    sink = pa.BufferOutputStream()
    chosen = table.select(keep).combine_chunks()
    with pa.ipc.new_stream(sink, chosen.schema) as writer:
        writer.write_table(chosen)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


@dataclass
class Entry:
    path: str  # relative to the data root, forward slashes
    layer: str
    bytes: int
    sha256: str
    rows: int | None = None
    content_sha256: str | None = None

    def key(self) -> str:
        """What must match on a rebuild: the content checksum if there is one, else the bytes."""
        return self.content_sha256 or self.sha256


def describe_file(root: Path, path: Path) -> Entry:
    rel = path.relative_to(root).as_posix()
    layer = rel.split("/")[1] if rel.startswith("local/") else "raw"
    entry = Entry(rel, layer, path.stat().st_size, file_sha256(path))
    if path.suffix == ".parquet":
        meta = pq.read_metadata(path)
        entry.rows = meta.num_rows
        names = set(meta.schema.names)
        if names & set(VOLATILE_COLUMNS):
            entry.content_sha256 = content_sha256(pq.read_table(path))
    return entry


def latest_raw_folders(root: Path) -> list[Path]:
    """The latest download folder of each economic data source (raw/macro/<source>/<date>)."""
    base = Path(root) / "raw" / "macro"
    if not base.exists():
        return []
    out = []
    for source in sorted(p for p in base.iterdir() if p.is_dir()):
        dates = sorted(p for p in source.iterdir() if p.is_dir())
        if dates:
            out.append(dates[-1])
    return out


def snapshot_files(root: Path) -> list[Path]:
    root = Path(root)
    files: list[Path] = sorted((root / "raw" / "freddie" / "sample").glob("*.zip"))
    for folder in latest_raw_folders(root):
        files += sorted(p for p in folder.iterdir() if p.is_file())
    for layer in ("bronze", "silver", "panel"):
        files += sorted((root / "local" / layer).rglob("*.parquet"))
    return files


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()


@dataclass
class Manifest:
    name: str
    quarter_end: str
    created_at: str
    env: str
    code_commit: str
    code_tree: str
    code_clean: bool
    files: list[Entry] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "files"}
        d["files"] = [e.__dict__ for e in self.files]
        return d

    def fingerprint(self) -> str:
        """One checksum for the whole data snapshot: the sorted (path, key) pairs."""
        h = hashlib.sha256()
        for e in sorted(self.files, key=lambda e: e.path):
            h.update(f"{e.path}\t{e.key()}\n".encode())
        return h.hexdigest()


def build(
    root: Path,
    quarter: str,
    version: int = 1,
    notes: dict[str, Any] | None = None,
    code: dict[str, Any] | None = None,
) -> Manifest:
    """Describe every snapshot file under `root`. `code` overrides the git lookup (tests)."""
    root = Path(root)
    if code is None:
        code = {
            "code_commit": git("rev-parse", "HEAD"),
            "code_tree": git("rev-parse", "HEAD^{tree}"),
            "code_clean": git("status", "--porcelain", "--untracked-files=no") == "",
        }
    files = snapshot_files(root)
    if not files:
        raise FileNotFoundError(f"nothing to snapshot under {root}")
    return Manifest(
        name=snapshot_name(quarter, version),
        quarter_end=quarter_end(quarter),
        created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        env=environment.current_env(),
        files=[describe_file(root, p) for p in files],
        notes=notes or {},
        **code,
    )


def write(root: Path, manifest: Manifest) -> Path:
    folder = snapshot_dir(root, manifest.name)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    body = manifest.as_dict() | {"fingerprint": manifest.fingerprint()}
    path.write_text(json.dumps(body, indent=1), encoding="utf-8")
    return path


def load(path: Path) -> Manifest:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    d.pop("fingerprint", None)
    files = [Entry(**e) for e in d.pop("files")]
    return Manifest(files=files, **d)


@dataclass
class Comparison:
    matched: int = 0
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # in the snapshot, not on disk now
    extra: list[str] = field(default_factory=list)  # on disk now, not in the snapshot

    @property
    def ok(self) -> bool:
        return not (self.changed or self.missing or self.extra)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "matched": self.matched, "changed": self.changed,
                "missing": self.missing, "extra": self.extra}  # fmt: skip


def compare(saved: Manifest, current: Iterable[Entry]) -> Comparison:
    now = {e.path: e for e in current}
    before = {e.path: e for e in saved.files}
    result = Comparison()
    for path, entry in sorted(before.items()):
        if path not in now:
            result.missing.append(path)
        elif now[path].key() != entry.key() or now[path].rows != entry.rows:
            result.changed.append(path)
        else:
            result.matched += 1
    result.extra = sorted(set(now) - set(before))
    return result


def verify(root: Path, manifest_path: Path) -> Comparison:
    """Re-describe the files on disk now and compare them with a saved manifest (control C08)."""
    saved = load(manifest_path)
    current = [describe_file(Path(root), p) for p in snapshot_files(Path(root))]
    return compare(saved, current)


def tag_message(manifest: Manifest) -> str:
    """Annotated tag message: a readable summary line, then the full manifest as JSON."""
    rows = {
        layer: sum(e.rows or 0 for e in manifest.files if e.layer == layer) for layer in ("bronze", "panel")
    }
    head = (
        f"{manifest.name}: data snapshot for the quarter ending {manifest.quarter_end} "
        f"({len(manifest.files)} files; bronze {rows['bronze']:,} rows; panel {rows['panel']:,} rows; "
        f"fingerprint {manifest.fingerprint()[:16]})"
    )
    body = manifest.as_dict() | {"fingerprint": manifest.fingerprint()}
    return head + "\n\n" + json.dumps(body, indent=1) + "\n"
