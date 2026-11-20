"""Compare the Databricks transition slice with the PC's build, row by row (Phase 2 plan, multi-state, M2).

    CECL_ENV=local python -m scripts.compare_transitions --years 2005,2009,2019

1. Builds the same vintages on the PC (DuckDB, the reference; tie-outs enforced).
2. Downloads the newest transition slice the Databricks `transition_features` job wrote.
3. Compares every row of the transition table on (loan, month) and of the projection start table
   on (loan): the same rows on both sides, and every column equal (numbers to 1e-9 relative, missing
   values in the same places).
4. Reads the job's compute minutes and projects the full build (28 vintages) from the panel rows.
Writes a run-log record (`transitions_compare`). Prints counts only, never loan rows.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from cecl import environment
from cecl import features as fx
from ingestion import feature_build as fb
from ingestion import run_log
from scripts import compare_features as cf

JOB = "cecl_lab_transition_features_dev"
TRANSITION_COLUMNS = ("split", "state_prev", "state", "weight", "property_state", *fx.TRANSITION_FEATURES)
START_COLUMNS = ("state_prev", "state", "property_state", "upb_start", "rate_start", "remaining_start",
                 "orig_ltv", "hpi_orig", *fx.TRANSITION_FEATURES)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--years", required=True)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--memory-limit", default="1500MB")
    args = parser.parse_args(argv)
    environment.current_env()
    years = cf.parse_years(args.years)

    import duckdb

    with run_log.logged_run(args.root, "transitions_compare", years=args.years) as run:
        con = duckdb.connect(str(args.root / "local" / "cecl_lab.duckdb"), read_only=True)
        tmp_dir = args.root / "local" / "tmp_duckdb"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"set memory_limit = '{args.memory_limit}'; set threads = 2; "
                    f"set temp_directory = '{tmp_dir.as_posix()}'")  # fmt: skip
        for y in years:
            r = fb.build_transitions_vintage(con, args.root, y)
            print(f"  PC {y}: {sum(r.rows_by_split.values()):,} transition rows, {r.projection_loans:,} loans "
                  f"alive in 2017-01, {len(r.tie_outs)} tie-outs agree")  # fmt: skip

        name, remote = cf.newest_result("transitions_")
        if remote["vintages"] != years:
            raise ValueError(f"the Databricks result covers {remote['vintages']}, not {years}")
        report: dict[str, Any] = {"result_file": name}
        local_root = args.root / "local"
        with tempfile.TemporaryDirectory() as tmp:
            cf.download(
                remote["export"].replace("/Volumes/", "dbfs:/Volumes/"), Path(tmp) / "slice", recursive=True
            )
            for kind, files, key, cols in (
                ("transitions", [p for base in ("transitions", "transitions_holdout")
                                 for p in (local_root / base).rglob("vintage=*.parquet")],
                 ("loan_id", "period_ym"), TRANSITION_COLUMNS),
                ("projection_start", list((local_root / "projection" / f"split={fx.VALIDATE}" / "start").rglob("vintage=*.parquet")),
                 ("loan_id",), START_COLUMNS),
            ):  # fmt: skip
                local = fb.parquet_relation(
                    [p.as_posix() for p in files if int(p.stem.split("=")[1]) in years]
                )
                remote_glob = (Path(tmp) / "slice" / kind).as_posix() + "/*/*.parquet"
                sql = cf.compare_sql(local, f"read_parquet('{remote_glob}')", cols)
                if key == ("loan_id",):
                    sql = sql.replace(" and l.period_ym = r.period_ym", "")
                cur = con.execute(sql)
                row = dict(zip([d[0] for d in cur.description], cur.fetchall()[0], strict=True))
                report[kind] = row
                differing = {c: row[c] for c in cols if row[c]}
                print(f"  {kind}: {row['matched']:,} rows matched; only on the PC {row['only_pc']:,}; "
                      f"only on Databricks {row['only_databricks']:,}; columns differing: {differing or 'none'}")  # fmt: skip
        job_run = cf.latest_run(JOB)
        minutes = cf.job_minutes(job_run)
        rows = dict(con.execute(
            "select vintage_year, count(*) from read_parquet('" + (local_root / "panel").as_posix()
            + "/vintage=*/panel.parquet') group by 1").fetchall())  # fmt: skip
        slice_rows = sum(rows[y] for y in years)
        projected = minutes["minutes"] * sum(rows.values()) / slice_rows
        report["minutes"] = minutes | {"projected_full_minutes": round(projected, 1)}
        run.databricks_minutes = minutes["minutes"]
        run.details = report
        clean = all(report[k]["only_pc"] == 0 and report[k]["only_databricks"] == 0 and
                    not any(report[k][c] for c in cols_) for k, cols_ in
                    (("transitions", TRANSITION_COLUMNS), ("projection_start", START_COLUMNS)))  # fmt: skip
        print(f"Databricks minutes {minutes['minutes']}; full build projected about {projected:.0f} minutes; "
              f"{'0 differences' if clean else 'DIFFERENCES FOUND'}")  # fmt: skip
        return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
