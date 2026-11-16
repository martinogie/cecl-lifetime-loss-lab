"""The run log (control C18): one record per run, written whether the run succeeds or fails.

Each run writes one small JSON file, `<root>/local/ops/run_log/<run_id>.json` (the local stand-in
for the `ops.run_log` table; Phase 1 step 9 adds the Databricks table). One file per run means two
runs at the same time (an Airflow task and a run on the PC) can never overwrite each other, and a
crashed run never corrupts earlier records.

A record holds: what ran (pipeline, environment, the years or sources, the code version), when and
for how long, how it ended (success or failed, with the error), rows per layer, contract results,
tie-outs, dbt test results, the counts the limitations log asks to watch (L-013, L-014, L-015,
XX at loan age 0), stale sources, alerts sent, and Databricks minutes (empty until step 9).

Use it as a context manager: the record is written on the way out, even if the run raises.

    with run_log.logged_run(root, "freddie", years=[2007]) as run:
        run.rows["bronze_perf"] = 1234
        run.tie_outs += [t.as_dict() for t in checked]
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cecl import environment

ERROR_LIMIT = 2000  # characters of the error message kept in the record


def log_dir(root: Path) -> Path:
    return Path(root) / "local" / "ops" / "run_log"


def code_version() -> str:
    """The git commit the code came from: CECL_CODE_VERSION if set (Airflow images), else git."""
    if os.environ.get("CECL_CODE_VERSION"):
        return os.environ["CECL_CODE_VERSION"]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, check=True, timeout=10
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


@dataclass
class RunRecord:
    pipeline: str  # "freddie", "macro", "health_check"...
    env: str
    run_id: str
    started_at: str
    code_version: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # running -> success | failed
    finished_at: str | None = None
    duration_seconds: float | None = None
    error: str | None = None
    rows: dict[str, int] = field(default_factory=dict)  # rows per layer / table
    contracts: list[dict[str, Any]] = field(default_factory=list)
    tie_outs: list[dict[str, Any]] = field(default_factory=list)
    tests: dict[str, Any] = field(default_factory=dict)  # dbt: pass / fail / warn counts
    watch_counts: dict[str, Any] = field(default_factory=dict)  # L-013, L-014, L-015, XX
    stale: dict[str, Any] = field(default_factory=dict)
    alerts: dict[str, str] = field(default_factory=dict)
    databricks_minutes: float | None = None  # Phase 1 step 9
    details: dict[str, Any] = field(default_factory=dict)


def new_record(pipeline: str, params: dict[str, Any] | None = None, run_id: str | None = None) -> RunRecord:
    now = datetime.now(UTC).replace(microsecond=0)
    return RunRecord(
        pipeline=pipeline,
        env=environment.current_env(),
        run_id=run_id or f"{now:%Y%m%dT%H%M%SZ}_{pipeline}_{uuid.uuid4().hex[:6]}",
        started_at=now.isoformat(),
        code_version=code_version(),
        params=params or {},
    )


def finish(record: RunRecord, error: BaseException | None = None) -> RunRecord:
    end = datetime.now(UTC).replace(microsecond=0)
    record.finished_at = end.isoformat()
    record.duration_seconds = (end - datetime.fromisoformat(record.started_at)).total_seconds()
    record.status = "failed" if error else "success"
    if error is not None:
        record.error = f"{type(error).__name__}: {error}"[:ERROR_LIMIT]
    return record


def write(root: Path, record: RunRecord) -> Path:
    """Write the record atomically (temporary name, then rename)."""
    folder = log_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{record.run_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(record), indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_all(root: Path) -> list[dict[str, Any]]:
    """Every record, oldest first."""
    folder = log_dir(root)
    if not folder.exists():
        return []
    records = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
    return sorted(records, key=lambda r: (r["started_at"], r["run_id"]))


def last_success(root: Path, pipeline: str) -> dict[str, Any] | None:
    done = [r for r in read_all(root) if r["pipeline"] == pipeline and r["status"] == "success"]
    return done[-1] if done else None


@contextmanager
def logged_run(root: Path, pipeline: str, run_id: str | None = None, **params: Any) -> Iterator[RunRecord]:
    """Yield a record to fill in; write it on the way out, marked success or failed."""
    record = new_record(pipeline, params, run_id)
    try:
        yield record
    except BaseException as e:
        write(root, finish(record, e))
        raise
    write(root, finish(record))
