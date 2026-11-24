"""The quarterly close's steps on the VM (Phase 8; DAG `cecl_quarterly_close`), kept here so they are tested.

The DAG calls these in order; each raises on a problem, so the close stops before anything is booked:
1. `check_request`: the quarter-end asked for is a quarter-end, later than the last close, not in the future;
2. `vm_commit` and `check_deploys`: every prod job was deployed from the commit the VM runs (control C08);
3. `check_freddie`: every origination year's sample zip is in the volume and newer than the last close
   (Martins downloads and uploads them; ADR-001);
4. `upload_macro`: the VM's economic tables go to the volume, for the `load_macro_tables` job;
5. `projection_params`: the projection runs the registry's `@challenger` models (their training runs);
6. `fetch`: the close's aggregate record and the quarter's monitoring come back to the VM.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from cecl import close, environment, scheduling
from ingestion import databricks_api as dbx

CLOSE_JOBS = (
    "load_macro_tables",
    "bronze_load",
    "panel_build",
    "reserve_projection",
    "close_pricing",
    "monitoring",
)
MACRO_TABLES = ("state_monthly", "national_monthly", "long_run_averages", "fed_paths", "fed_paths_2025",
                "fed_paths_calendar", "household_monthly")  # fmt: skip
STEPS = ("current", "30", "60")
CLOSE_HOURS = 4  # bronze reload ~55 min, panel ~40, projection ~20, pricing ~15, monitoring ~15, with margin


class CloseRefused(RuntimeError):
    """The close cannot start or continue; the message says why and what to do."""


def volume() -> str:
    return "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"


def check_request(reserve_ym: str, today: date, last_close: str | None, rehearsal: bool = False) -> str:
    """The quarter-end to close: a quarter-end month, at least one full month in the past (Freddie's data for
    a quarter arrives months later), and after the last close. A **rehearsal** re-closes the last booked quarter
    instead (the whole chain in prod; it must reproduce the booked allowance, or the controls stop it)."""
    R = str(reserve_ym).strip()
    if not close.is_quarter_end(R):
        raise CloseRefused(f"reserve_ym must be a quarter-end month (YYYYMM), got {R!r}")
    if R >= f"{today.year}{today.month:02d}":
        raise CloseRefused(f"{R} has not ended yet")
    if rehearsal:
        if R != last_close:
            raise CloseRefused(f"a rehearsal re-closes the last booked quarter ({last_close}), not {R}")
        return R
    if last_close and R <= last_close:
        raise CloseRefused(f"{R} is not after the last close ({last_close}); a re-run of a past quarter is a "
                           "separate, deliberate step (runbook: re-running a past quarter)")  # fmt: skip
    if last_close and close.prior_quarter(R) != last_close:
        raise CloseRefused(f"{R} skips a quarter: the last close is {last_close}, so the next one is "
                           f"{close.next_quarter(last_close)}; quarters are closed in order")  # fmt: skip
    return R


def check_time_budget(now: datetime, hours: float = CLOSE_HOURS) -> None:
    """The close's jobs run for hours: refuse to start if a blocked window (Monday before 13:00, 02:30-04:00)
    begins before it would finish, so no step starts inside one (rule 6)."""
    t = now
    while t <= now + timedelta(hours=hours):
        reason = scheduling.blocked_reason(t)
        if reason:
            raise CloseRefused(f"the close takes up to {hours:g} hours and would reach a blocked window at "
                               f"{t.astimezone(scheduling.TZ):%a %H:%M} Chicago ({reason}); start it later")  # fmt: skip
        t += timedelta(minutes=15)


def check_overlay_review(log_path: Path, R: str) -> int:
    """ADR-006: overlays expire every quarter and the L-038 decision is reviewed each quarter, so the overlay log
    must hold this quarter-end's review (an overlay or an assessment dated R) before the close."""
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))
    entries = [e for e in log.get("overlays", []) + log.get("assessments", []) if e.get("reserve_ym") == R]
    if not entries:
        raise CloseRefused(f"no overlay review for {R} in {Path(log_path).name}: record this quarter's review "
                           "(ADR-006; maker and checker) and merge it before the close")  # fmt: skip
    return len(entries)


def last_close(ws: dbx.Workspace) -> str | None:
    names = [e["name"].strip("/") for e in ws.list_dir(f"{volume()}/results/close") if e["is_directory"]]
    done = [n for n in names if close.is_quarter_end(n)]
    return max(done) if done else None


def vm_commit(git_dir: Path) -> str:
    """The commit the VM's code is on, read from the repository's .git folder (mounted read-only)."""
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    ref = head[5:]
    loose = git_dir / ref
    if loose.exists():
        return loose.read_text(encoding="utf-8").strip()
    packed = git_dir / "packed-refs"
    for line in packed.read_text(encoding="utf-8").splitlines() if packed.exists() else []:
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return sha.strip()
    raise CloseRefused(f"cannot read the VM's commit ({ref})")


def check_deploys(
    ws: dbx.Workspace, env: str, commit: str, jobs: Iterable[str] = CLOSE_JOBS
) -> dict[str, str]:
    deployed = {j: ws.deployed_commit(dbx.job_name(j, env)) or "none" for j in jobs}
    wrong = {j: c[:12] for j, c in deployed.items() if c != commit}
    if wrong:
        raise CloseRefused(f"jobs deployed from another commit than the VM's {commit[:12]}: {wrong}. "
                           f"Deploy the bundle from this commit first (runbook: deploying an update)")  # fmt: skip
    return deployed


def check_freddie(ws: dbx.Workspace, years: Iterable[int], newer_than: datetime | None) -> dict[str, Any]:
    entries = {e["name"]: e for e in ws.list_dir(f"{volume()}/freddie/sample")}
    want = [f"sample_{y}.zip" for y in years]
    missing = [w for w in want if w not in entries]
    if missing:
        raise CloseRefused(f"Freddie sample files missing from the volume: {missing}")
    if newer_than is not None:
        cut = newer_than.timestamp() * 1000
        old = [w for w in want if entries[w]["last_modified"] <= cut]
        if old:
            raise CloseRefused(f"Freddie files not updated since the last close: {old[:5]}{'...' if len(old) > 5 else ''} "
                               "(download Freddie's new release and upload it first)")  # fmt: skip
    return {"files": len(want), "bytes": sum(entries[w]["size"] for w in want)}


def upload_macro(ws: dbx.Workspace, data_root: Path, target: str = "macro_silver") -> list[str]:
    """The VM's economic tables to the volume folder the `load_macro_tables` job reads (`macro_silver`); a
    rehearsal sends them to `macro_silver_rehearsal` instead, so the booked economy is not replaced."""
    folder = Path(data_root) / "local" / "silver" / "macro"
    sent = []
    for t in MACRO_TABLES:
        f = folder / f"{t}.parquet"
        if f.exists():
            ws.upload(f.read_bytes(), f"{volume()}/{target}/{t}.parquet")
            sent.append(t)
    if not {"state_monthly", "national_monthly", "long_run_averages", "fed_paths"} <= set(sent):
        raise CloseRefused(f"the VM's economic tables are incomplete ({sent}); run cecl_macro_data first")
    return sent


def projection_params(ws: dbx.Workspace, R: str) -> dict[str, str]:
    runs = {}
    for step in STEPS:
        name = environment.registered_model(f"pd_v2_transition_{step}")
        runs[step] = ws.model_alias(name, environment.CHALLENGER)["run_id"]
        if not runs[step]:
            raise CloseRefused(f"{name}@{environment.CHALLENGER} has no training run")
    return {"runs": json.dumps(runs), "plan": f"{R}@{R},{R}@{close.prior_quarter(R)}", "paths": "8"}


def fetch(ws: dbx.Workspace, R: str, data_root: Path) -> dict[str, Path]:
    """The close's aggregate record and the quarter's monitoring, copied to the VM (data/close/<R>/)."""
    out = Path(data_root) / "close" / R
    out.mkdir(parents=True, exist_ok=True)
    files = {"close": (f"{volume()}/results/close/{R}/close.json", out / "close.json"),
             "monitoring": (f"{volume()}/monitoring/quarterly/{R}.json", out / "monitoring.json")}  # fmt: skip
    for remote, local in files.values():
        local.write_bytes(ws.download(remote))
    return {k: v[1] for k, v in files.items()}


def summary_line(rec: Mapping[str, Any], mon: Mapping[str, Any]) -> str:
    R = rec["reserve_ym"]
    d = rec["dates"][R]
    rf = rec["rollforward"]["total"]
    return (f"close {R}: total allowance ${d['total_allowance'] / 1e6:,.1f}m ({d['total_allowance_ratio']:.2%}); "
            f"rollforward {rf['start']} + {rf['provision']} {rf['write_offs']} + {rf['recoveries']} = {rf['end']} "
            f"(to the cent: {rec['rollforward']['reconciles_to_the_cent']}); controls pass: {rec['controls']['all_pass']}; "
            f"monitoring: {mon.get('default_cells_failed', 0)} default cells outside the bar, retrain due: "
            f"{(mon.get('retrain') or {}).get('due', False)}; committee pack draft ready for review")  # fmt: skip
