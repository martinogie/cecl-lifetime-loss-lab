"""Compare Databricks with the PC, number for number, and record the run (Phase 1 step 9).

Downloads the newest tie-out result the Databricks `panel_build` job wrote to the bronze volume,
computes the same numbers on the PC (same SQL, DuckDB on the local layers), and lists every
difference: every tie-out value, every watch count, and how each vintage's loans ended (defaulted,
prepaid, censored: the basis of default rates). Then writes a run-log record with the Databricks
minutes of the latest runs of both jobs (from the Jobs API: compute time, against the Free Edition
quota shared with Bank Health, L-009).

    python -m scripts.compare_databricks --years 1999-2026

Needs CECL_ENV and the Databricks CLI profile `cecl` (Martins's; the token is never read here).
Prints counts and totals only, never loan rows.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cecl import environment
from ingestion import run_log, tie_outs

PROFILE = "cecl"
JOBS = ("cecl_lab_bronze_load_dev", "cecl_lab_panel_build_dev")
RESULTS = "dbfs:/Volumes/cecl_lab_dev/bronze/raw/results"


def cli(*args: str) -> Any:
    out = subprocess.run(
        ["databricks", *args, "--profile", PROFILE, "-o", "json"], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out) if out.strip() else None


def newest_result(folder: str) -> dict[str, Any]:
    listing = cli("fs", "ls", folder)
    names = sorted(e["name"] for e in listing if e["name"].startswith("tie_outs_"))
    if not names:
        raise FileNotFoundError(f"no tie-out results in {folder}")
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / names[-1]
        subprocess.run(
            ["databricks", "fs", "cp", f"{folder}/{names[-1]}", str(local), "--profile", PROFILE],
            capture_output=True,
            check=True,
        )
        result = json.loads(local.read_text(encoding="utf-8"))
    result["file"] = names[-1]
    return result


def job_minutes(job_names: tuple[str, ...]) -> dict[str, Any]:
    """Compute minutes of the latest completed run of each job (sum of its tasks' execution time)."""
    jobs = {j["settings"]["name"]: j["job_id"] for j in cli("jobs", "list")}
    out: dict[str, Any] = {}
    for name in job_names:
        runs = cli("jobs", "list-runs", "--job-id", str(jobs[name]), "--completed-only", "--limit", "1")
        run = cli("jobs", "get-run", str(runs[0]["run_id"]))
        ms = sum(t.get("execution_duration", 0) for t in run.get("tasks", []))
        out[name] = {
            "run_id": run["run_id"],
            "result": run.get("state", {}).get("result_state"),
            "minutes": round(ms / 60000, 1),
        }
    return out


def local_result(root: Path, years: list[int]) -> dict[str, Any]:
    import duckdb

    local = root / "local"
    con = duckdb.connect(str(local / "cecl_lab.duckdb"), read_only=True)
    try:
        rel = tie_outs.local_relations(local / "bronze", local / "panel")
        checks, watch, rows = tie_outs.layer_tie_outs(lambda sql: con.execute(sql).fetchall(), rel, years)
        outcomes = tie_outs.panel_outcomes(lambda sql: con.execute(sql).fetchall(), rel["panel"], years)
    finally:
        con.close()
    return tie_outs.comparable(checks, watch, outcomes) | {"rows": rows}


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--years", required=True)
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    environment.current_env()
    years = parse_years(args.years)

    with run_log.logged_run(args.root, "databricks", years=args.years) as run:
        remote = newest_result(RESULTS)
        if remote.get("vintages") != years:
            raise ValueError(f"the Databricks result covers {remote.get('vintages')}, not {years}")
        mine = local_result(args.root, years)
        keys = ("tie_outs", "watch", "outcomes", "rows")
        found = tie_outs.differences({k: mine[k] for k in keys}, {k: remote[k] for k in keys})
        minutes = job_minutes(JOBS)
        run.databricks_minutes = sum(m["minutes"] for m in minutes.values())
        run.rows = {f"databricks_{k}": v for k, v in remote["rows"].items()}
        run.tie_outs = [{"name": n, "expected": e, "actual": a, "ok": e == a or tie_outs.differences(
            {"x": e}, {"x": a}) == []} for n, (e, a) in remote["tie_outs"].items()]  # fmt: skip
        run.details = {"result_file": remote["file"], "jobs": minutes, "differences": found[:50]}
        agree = sum(t["ok"] for t in run.tie_outs)
        print(f"Databricks tie-outs: {agree} of {len(run.tie_outs)} agree (file {remote['file']})")
        print(f"compared with the PC: {len(found)} differences in tie-outs, watch counts, outcomes, rows")
        for line in found[:20]:
            print("  ", line)
        print(f"Databricks compute: {minutes} -> {run.databricks_minutes} minutes")
        if found:
            raise RuntimeError(f"Databricks and the PC differ in {len(found)} places")
    print(f"run log: {run_log.log_dir(args.root) / (run.run_id + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
