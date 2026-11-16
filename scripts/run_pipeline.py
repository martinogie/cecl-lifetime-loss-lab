"""Run the data foundation end to end on the PC, with tie-outs and a run-log record.

Steps (each can be skipped): Freddie zips -> bronze; dbt builds silver and runs its tests; the
ADR-002 panel; the C02 tie-outs between the layers; the economic data (download, or replay of a
saved download). Every run writes one run-log record (data/local/ops/run_log/), success or failure,
and a failure sends an alert (skipped cleanly on a laptop or when no webhook is set).

    python -m scripts.run_pipeline --years 1999-2026                       # full run
    python -m scripts.run_pipeline --years 2007 --macro skip               # backfill one vintage
    python -m scripts.run_pipeline --macro replay --macro-date 2026-11-15  # rebuild economic data
                                                                           # from a saved download

A backfill is the same command for chosen years: bronze, silver views, the panel and the tie-outs
are all rebuilt for those vintages and replace what was there (every step overwrites atomically).

Needs CECL_ENV. Prints counts and totals only, never loan rows.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from cecl import controls, environment, scheduling
from ingestion import alerts, disk, freddie, macro_build, panel_build, run_log, tie_outs

REPO = Path(__file__).resolve().parents[1]
DBT_DIR = REPO / "dbt"


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


def paths(root: Path) -> dict[str, Path]:
    root = root.resolve()
    return {
        "zips": root / "raw" / "freddie" / "sample",
        "bronze": root / "local" / "bronze",
        "panel": root / "local" / "panel",
        "duckdb": root / "local" / "cecl_lab.duckdb",
    }


def load_bronze(p: dict[str, Path], years: list[int], release: int, run: run_log.RunRecord) -> None:
    for year in years:
        zip_path = p["zips"] / f"sample_{year}.zip"
        if not zip_path.exists():
            raise FileNotFoundError(f"no sample zip for {year}: {zip_path.name}")
        for f in freddie.load_zip(zip_path, release, p["bronze"]):
            run.contracts.append({"file": f.member, "contract": "pass", "rows": f.rows, "tie_out": "ok"})
            print(f"  bronze {year} {f.kind}: {f.rows:,} rows (contract and row tie-out ok)")


def dbt_executable() -> str:
    found = shutil.which("dbt") or str(Path(sys.executable).parent / "dbt")
    return found


def run_dbt(p: dict[str, Path], run: run_log.RunRecord) -> None:
    """dbt build (silver views and their tests) on the local DuckDB file; results into the log."""
    env = os.environ | {"CECL_DUCKDB_PATH": str(p["duckdb"]), "CECL_BRONZE_PATH": p["bronze"].as_posix()}
    result = subprocess.run(
        [dbt_executable(), "build", "--profiles-dir", ".", "--quiet"],
        cwd=DBT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    results_file = DBT_DIR / "target" / "run_results.json"
    counts: dict[str, int] = {}
    if results_file.exists():
        for r in json.loads(results_file.read_text(encoding="utf-8"))["results"]:
            kind = r["unique_id"].split(".")[0]
            counts[f"{kind}_{r['status']}"] = counts.get(f"{kind}_{r['status']}", 0) + 1
    run.tests["dbt"] = counts
    print(f"  dbt build: {counts}")
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-15:]
        raise RuntimeError("dbt build failed:\n" + "\n".join(tail))


def _panel_one(args: tuple[str, int, str]) -> dict[str, Any]:
    database, vintage, out = args
    started = time.monotonic()
    summary = panel_build.build_from_duckdb(Path(database), vintage, Path(out)).as_dict()
    summary["seconds"] = round(time.monotonic() - started, 1)
    return summary


def build_panel(p: dict[str, Path], years: list[int], workers: int, run: run_log.RunRecord) -> None:
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_panel_one, [(str(p["duckdb"]), y, str(p["panel"])) for y in years]))
    run.details["panel"] = {str(s["vintage"]): {k: s[k] for k in ("loans", "loan_months", "final_status")}
                            for s in results}  # fmt: skip
    print(
        f"  panel: {sum(s['loans'] for s in results):,} loans, {sum(s['loan_months'] for s in results):,} rows"
    )


def check_tie_outs(
    p: dict[str, Path], years: list[int], run: run_log.RunRecord, simulate_failure: bool
) -> None:
    import duckdb

    con = duckdb.connect(str(p["duckdb"]), read_only=True)
    try:
        checks, watch, rows = tie_outs.freddie_tie_outs(con, p["bronze"], p["panel"], years)
    finally:
        con.close()
    if simulate_failure:
        # Demonstration only (local and dev): pretend the source had one more loan than silver.
        checks[0] = replace(checks[0], expected=checks[0].expected + 1)
        run.details["simulated_tie_out_failure"] = checks[0].name
    run.tie_outs = [t.as_dict() for t in checks]  # logged before enforcing, so failures are visible
    run.watch_counts = {"total": tie_outs.totals(watch), "by_vintage": watch}
    run.rows.update(rows)
    controls.enforce(checks)
    print(f"  tie-outs: {len(checks)} of {len(checks)} agree exactly")


def run_macro(root: Path, mode: str, when: date | None, run: run_log.RunRecord) -> None:
    if mode == "download":
        today = datetime.now(scheduling.TZ).date()
        summaries: dict[str, Any] = {
            "fred": macro_build.load_fred(root, today),
            "fhfa": macro_build.load_fhfa(root, today),
            "fed": macro_build.load_fed(root, today),
        }
        summaries["silver"] = macro_build.build_silver(root)
    else:
        if when is None:
            raise ValueError("--macro replay needs --macro-date (a folder under data/raw/macro/<source>/)")
        summaries = macro_build.replay(root, when)
    for name in ("fred", "fhfa", "fed"):
        run.rows[f"bronze_macro_{name}"] = summaries[name]["bronze_rows"]
        run.contracts.append({"file": f"macro_{name}", "contract": summaries[name]["contract"],
                              "checked": summaries[name]["series_checked"]})  # fmt: skip
    for table, n in summaries["silver"]["silver_rows"].items():
        run.rows[f"silver_macro_{table}"] = n
    run.stale = macro_build.stale_sources(summaries)
    run.details["macro"] = {
        k: summaries["silver"][k] for k in ("anchors", "severely_adverse_worst", "fed_timing")
    }
    print(f"  economic data ({mode}): contracts pass; stale: {run.stale or 'none'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--years", help="vintages, e.g. 1999-2026 or 2007,2008")
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--release", type=int, default=48)
    parser.add_argument("--skip-bronze", action="store_true")
    parser.add_argument("--skip-dbt", action="store_true")
    parser.add_argument("--skip-panel", action="store_true")
    parser.add_argument("--macro", choices=("skip", "download", "replay"), default="skip")
    parser.add_argument("--macro-date", type=date.fromisoformat)
    parser.add_argument("--workers", type=int, default=max(1, min(3, (os.cpu_count() or 2) - 1)))
    parser.add_argument("--simulate-tie-out-failure", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    env = environment.current_env()  # CECL_ENV is required, as everywhere
    if env in environment.AIRFLOW_ENVIRONMENTS:
        scheduling.refuse_blocked_window()  # the shared VM's rules apply to dev and prod runs
    if args.simulate_tie_out_failure and env not in ("local", "dev"):
        parser.error("--simulate-tie-out-failure is for local and dev only")
    years = parse_years(args.years) if args.years else []
    if not years and args.macro == "skip":
        parser.error("nothing to do: give --years and/or --macro")

    p = paths(args.root)
    params = {"years": args.years, "release": args.release, "macro": args.macro,
              "macro_date": args.macro_date, "skipped": [s for s in ("bronze", "dbt", "panel")
                                                         if getattr(args, f"skip_{s}")]}  # fmt: skip
    started = time.monotonic()
    print(f"environment: {env}; {disk.describe(p['bronze'].parent)}")
    try:
        with run_log.logged_run(args.root, "freddie" if years else "macro", **params) as run:
            try:
                if years:
                    if not args.skip_bronze:
                        load_bronze(p, years, args.release, run)
                    if not args.skip_dbt:
                        run_dbt(p, run)
                    if not args.skip_panel:
                        build_panel(p, years, args.workers, run)
                    check_tie_outs(p, years, run, args.simulate_tie_out_failure)
                if args.macro != "skip":
                    run_macro(args.root, args.macro, args.macro_date, run)
                    if run.stale:
                        run.alerts = alerts.send(f"stale economic data: {sorted(run.stale)}")
            except Exception as e:
                run.alerts = alerts.send(
                    f"FAILED local pipeline {run.run_id}: {type(e).__name__}: {str(e)[:300]}"
                )
                raise
    except Exception as e:  # noqa: BLE001 (report, then exit non-zero)
        print(f"\nRUN FAILED after {time.monotonic() - started:.0f}s: {type(e).__name__}: {e}")
        print(f"run log: {run_log.log_dir(args.root) / (run.run_id + '.json')}")
        return 1
    print(f"\nrun {run.run_id} succeeded in {time.monotonic() - started:.0f}s")
    if run.watch_counts:
        print(f"watch counts: {run.watch_counts['total']}")
    print(f"run log: {run_log.log_dir(args.root) / (run.run_id + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
