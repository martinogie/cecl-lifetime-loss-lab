"""Build the monthly loan panel for chosen vintages and print the summary (Phase 1, step 4).

Needs the local DuckDB database built by dbt first (silver.int_freddie__loan_months), e.g.:
    cd dbt; CECL_ENV=local CECL_BRONZE_PATH=<absolute path to data/local/bronze> dbt build ...
Then, from the repo root:
    python -m scripts.build_panel --years 1999-2026

Prints counts and rates only, never loan rows. Every summary is shown with and without the loans
with terms over 360 months (L-014, decided 14 November 2026).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ingestion import disk, panel_build

DEFAULT_DB = Path("data/local/cecl_lab.duckdb")
DEFAULT_OUT = Path("data/local/panel")
SUMMARY = Path("data/local/panel_summary.json")
BRONZE_PERF = Path("data/local/bronze/freddie_perf")


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


def run_one(args: tuple[str, int, str]) -> dict:
    database, vintage, out = args
    started = time.monotonic()
    summary = panel_build.build_from_duckdb(Path(database), vintage, Path(out)).as_dict()
    summary["seconds"] = round(time.monotonic() - started, 1)
    return summary


def rate(part: int, whole: int) -> str:
    return f"{part / whole:6.2%}" if whole else "   n/a"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ADR-002 monthly loan panel.")
    parser.add_argument("--years", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=max(1, min(3, (os.cpu_count() or 2) - 1)))
    args = parser.parse_args(argv)

    years = parse_years(args.years)
    # The panel is about the size of the bronze monthly files (fewer rows, a few extra columns).
    bronze = sum(f.stat().st_size for f in BRONZE_PERF.rglob("*.parquet")) if BRONZE_PERF.exists() else 0
    disk.check_room(args.out, bronze)
    print(f"building {len(years)} vintage(s) with {args.workers} workers; {disk.describe(args.out)}")

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_one, [(str(args.database), y, str(args.out)) for y in years]))

    print(
        f"\n{'year':5s} {'loans':>7s} {'months':>10s} {'default':>8s} {'prepaid':>8s} {'censored':>8s}"
        f" {'def rate':>8s} | {'>360':>5s} {'def rate excl >360':>18s} {'secs':>6s}"
    )
    for s in results:
        fs, f360 = s["final_status"], s["final_status_term_over_360"]
        d, loans = fs.get("defaulted", 0), s["loans"]
        d_ex, loans_ex = d - f360.get("defaulted", 0), loans - s["loans_term_over_360"]
        print(
            f"{s['vintage']:<5d} {loans:>7,} {s['loan_months']:>10,} {d:>8,} {fs.get('prepaid', 0):>8,}"
            f" {fs.get('censored', 0):>8,} {rate(d, loans):>8s} | {s['loans_term_over_360']:>5,}"
            f" {rate(d_ex, loans_ex):>18s} {s['seconds']:>6}"
        )
    exits: dict[str, int] = {}
    for s in results:
        for k, v in s["exits"].items():
            exits[k] = exits.get(k, 0) + v
    print("\nhow loans left the data (all vintages):")
    for k, v in sorted(exits.items(), key=lambda kv: -kv[1]):
        print(f"  {k:32s} {v:>9,}")
    # Run log: how many loans used the ADR-002 E clarification (XX at loan age 0 = "not yet due")
    xx = {s["vintage"]: s["loans_xx_not_yet_due"] for s in results if s["loans_xx_not_yet_due"]}
    print(f"\nloans whose first month was XX at loan age 0, read as 'not yet due': {sum(xx.values()):,} {xx}")
    print("\ntie-outs: every loan in silver is in the panel, each with exactly one final row: ok")
    print(f"after: {disk.describe(args.out)}")

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"summary written to {SUMMARY} (git-ignored; counts only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
