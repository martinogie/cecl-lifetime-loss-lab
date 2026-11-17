"""Build the model's feature table on the PC (Phase 2 step 2).

    CECL_ENV=local python -m scripts.build_features --years 1999-2026

Runs the preflight (memory, disk, dev Airflow stopped), builds one vintage at a time
(ingestion/feature_build.py), ties out every vintage (a mismatch stops the build), checks the
contract over the whole table, compares current LTV with Freddie's estimated LTV, and writes a
run-log record. Prints counts and summary figures only, never loan rows.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from cecl import controls, environment
from cecl import features as fx
from ingestion import feature_build as fb
from ingestion import ml_preflight, run_log

MIN_MEMORY_GB = 2.0  # DuckDB is capped at 1.5 GB; training itself needs 4 GB


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
    # On a busy PC: a lower DuckDB cap spills more to disk (slower, same results).
    parser.add_argument("--memory-limit", default=fb.MEMORY_LIMIT, help="DuckDB memory cap, e.g. 1000MB")
    parser.add_argument("--min-memory-gb", type=float, default=MIN_MEMORY_GB)
    args = parser.parse_args(argv)
    environment.current_env()
    years = parse_years(args.years)

    import duckdb

    pre = ml_preflight.enforce(ml_preflight.check(args.root, min_memory_gb=args.min_memory_gb))
    started = time.monotonic()
    with run_log.logged_run(args.root, "features", years=args.years, sample_every=fx.SAMPLE_EVERY,
                            sample_seed=fx.SAMPLE_SEED) as run:  # fmt: skip
        run.details["preflight"] = pre.as_dict() | {"duckdb_memory_limit": args.memory_limit}
        con = duckdb.connect(str(args.root / "local" / "cecl_lab.duckdb"), read_only=True)
        tmp = args.root / "local" / "tmp_duckdb"
        tmp.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"set memory_limit = '{args.memory_limit}'; set threads = 2; set temp_directory = '{tmp.as_posix()}'"
        )
        try:
            checks: list[controls.TieOut] = []
            by_split: dict[str, int] = {}
            censored = 0
            for y in years:
                t = time.monotonic()
                r = fb.build_vintage(con, args.root, y)
                checks += r.tie_outs
                censored += r.censored
                for s, n in r.rows_by_split.items():
                    by_split[s] = by_split.get(s, 0) + n
                print(
                    f"  {y}: {sum(r.rows_by_split.values()):>9,} rows {r.rows_by_split} ({time.monotonic() - t:.0f}s)"
                )
            run.tie_outs = [t.as_dict() for t in checks]
            run.rows = {f"features_{s}": n for s, n in by_split.items()} | {"censored_left_out": censored}
            controls.enforce(checks)
            print(f"tie-outs: {len(checks)} of {len(checks)} agree")

            paths = sorted(
                p.as_posix()
                for base in ("features", "features_holdout")
                for p in (args.root / "local" / base).rglob("vintage=*.parquet")
                if int(p.stem.split("=")[1]) in years
            )
            cursor = con.execute(fb.contract_sql(fb.parquet_relation(paths)))
            cols = [d[0] for d in cursor.description]
            contract = dict(zip(cols, cursor.fetchone() or (), strict=True))
            problems = fb.contract_problems(contract)
            missing = {
                n: round(contract[f"{n}__missing"], 4) for n in fx.FEATURE_NAMES if contract[f"{n}__missing"]
            }
            run.contracts = [{"file": "features", "contract": "fail" if problems else "pass", "problems": problems,
                              "missing_share": missing}]  # fmt: skip
            n, mad, corr, within10 = con.execute(
                fb.eltv_check_sql(fb.parquet_relation(paths))
            ).fetchone() or (0, 0.0, 0.0, 0.0)
            run.details["eltv_check"] = {"rows": n, "median_abs_diff_points": mad, "correlation": corr,
                                         "share_within_10_points": within10}  # fmt: skip
            print(f"contract: {'pass' if not problems else problems}; missing shares: {missing}")
            print(f"current LTV vs Freddie's estimated LTV: {n:,} rows, median difference {mad:.1f} points, "
                  f"correlation {corr:.3f}, within 10 points {within10:.1%}")  # fmt: skip
            if problems:
                raise ValueError("feature contract failed: " + "; ".join(problems))
        finally:
            con.close()
        run.details["peak_memory_gb"] = round(ml_preflight.peak_memory_gb(), 2)
    print(
        f"done in {time.monotonic() - started:.0f}s; rows by split: {by_split}; censored months left out: {censored:,}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
