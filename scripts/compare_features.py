"""Compare the Databricks feature slice with the PC's build, row by row (Phase 2 step 2a).

    CECL_ENV=local python -m scripts.compare_features --years 2005,2009,2019

1. Builds the same vintages on the PC (DuckDB, the reference; memory capped).
2. Downloads the newest feature slice and result file the Databricks `features` job wrote to the
   bronze volume (CLI profile `cecl`).
3. Compares every row of the training and horizon tables on (loan, month): the same rows kept on
   both sides, and every column equal (numbers to 1e-9 relative, missing values in the same places).
4. Checks the economic tables Databricks loaded against the PC's silver files (rows, value sums).
5. Reads the job's compute minutes and projects the full build (28 vintages) from the panel rows.
Writes a run-log record (`features_compare`). Prints counts only, never loan rows.
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
from cecl import features as fx
from ingestion import feature_build as fb
from ingestion import run_log

PROFILE = "cecl"
JOB = "cecl_lab_features_dev"
RESULTS = "dbfs:/Volumes/cecl_lab_dev/bronze/raw/results"
KEY = ("loan_id", "period_ym")
COLUMNS = ("split", "outcome", "weight", "property_state", *fx.FEATURE_NAMES)
TOLERANCE = 1e-9


def cli(*args: str) -> Any:
    out = subprocess.run(["databricks", *args, "--profile", PROFILE, "-o", "json"], capture_output=True, text=True,
                         check=True).stdout  # fmt: skip
    return json.loads(out) if out.strip() else None


def download(src: str, dst: Path, recursive: bool = False) -> None:
    args = ["databricks", "fs", "cp", src, str(dst), "--profile", PROFILE, "--overwrite"]
    subprocess.run(args + (["--recursive"] if recursive else []), capture_output=True, check=True)


def newest_result(prefix: str = "features_") -> tuple[str, dict[str, Any]]:
    names = sorted(e["name"] for e in cli("fs", "ls", RESULTS) if e["name"].startswith(prefix))
    names = [n for n in names if n.endswith(".json")]
    if not names:
        raise FileNotFoundError(f"no feature result in {RESULTS}")
    with tempfile.TemporaryDirectory() as tmp:
        download(f"{RESULTS}/{names[-1]}", Path(tmp) / names[-1])
        return names[-1], json.loads((Path(tmp) / names[-1]).read_text(encoding="utf-8"))


def compare_sql(local: str, remote: str, columns: tuple[str, ...] = COLUMNS) -> str:
    """Rows only on one side, and per column the rows that differ (DuckDB)."""
    diffs = []
    for c in columns:
        if c in ("split", "property_state"):
            cond = f"l.{c} is distinct from r.{c}"
        else:
            cond = (f"(l.{c} is null) <> (r.{c} is null) or abs(l.{c} - r.{c}) > "
                    f"{TOLERANCE} * greatest(1.0, abs(l.{c}))")  # fmt: skip
        diffs.append(
            f"count(*) filter (where l.loan_id is not null and r.loan_id is not null and ({cond})) as {c}"
        )
    on = " and ".join(f"l.{k} = r.{k}" for k in KEY)
    return (
        f"select count(*) filter (where r.loan_id is null) as only_pc, "
        f"count(*) filter (where l.loan_id is null) as only_databricks, "
        f"count(*) filter (where l.loan_id is not null and r.loan_id is not null) as matched, {', '.join(diffs)} "
        f"from {local} l full outer join {remote} r on {on}"
    )


def latest_run(job: str = JOB) -> dict[str, Any]:
    jobs = {j["settings"]["name"]: j["job_id"] for j in cli("jobs", "list")}
    runs = cli("jobs", "list-runs", "--job-id", str(jobs[job]), "--completed-only", "--limit", "1")
    return dict(cli("jobs", "get-run", str(runs[0]["run_id"])))


def job_minutes(run: dict[str, Any]) -> dict[str, Any]:
    tasks = {t["task_key"]: round(t.get("execution_duration", 0) / 60000, 2) for t in run.get("tasks", [])}
    return {"run_id": run["run_id"], "result": run.get("state", {}).get("result_state"), "minutes_by_task": tasks,
            "minutes": round(sum(tasks.values()), 2)}  # fmt: skip


def macro_output(run: dict[str, Any]) -> dict[str, Any]:
    """What notebook 35 returned: rows and value sums per economic table."""
    task = next(t for t in run["tasks"] if t["task_key"] == "load_macro")
    out = cli("jobs", "get-run-output", str(task["run_id"]))
    return dict(json.loads(out["notebook_output"]["result"])["macro"])


def macro_differences(con: Any, root: Path, remote: dict[str, Any]) -> list[str]:
    """Databricks' economic tables against the PC's silver files: rows and value sums per variable."""
    found = []
    for name, info in remote.items():
        path = (root / "local" / "silver" / "macro" / f"{name}.parquet").as_posix()
        cols = [d[0] for d in con.execute(f"select * from read_parquet('{path}') limit 0").description]
        keys = [c for c in ("scenario", "state", "geo") if c in cols][:1] + ["variable"]
        value = "path_value" if name == "fed_paths" else "value"
        rows = con.execute(f"select count(*) from read_parquet('{path}')").fetchall()[0][0]
        if rows != info["rows"]:
            found.append(f"{name}: {info['rows']} rows on Databricks, {rows} on the PC")
        local = {"|".join(str(v) for v in r[:-1]): r[-1] for r in con.execute(
            f"select {', '.join(keys)}, sum({value}) from read_parquet('{path}') group by all").fetchall()}  # fmt: skip
        for k, v in info["value_sums"].items():
            if k not in local or abs(local[k] - v) > TOLERANCE * max(1.0, abs(v)):
                found.append(f"{name} {k}: {v} on Databricks, {local.get(k)} on the PC")
        found += [f"{name} {k}: only on the PC" for k in set(local) - set(info["value_sums"])]
    return found


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
    parser.add_argument("--memory-limit", default="600MB")
    parser.add_argument("--skip-local-build", action="store_true")
    args = parser.parse_args(argv)
    environment.current_env()
    years = parse_years(args.years)

    import duckdb

    with run_log.logged_run(args.root, "features_compare", years=args.years) as run:
        con = duckdb.connect(str(args.root / "local" / "cecl_lab.duckdb"), read_only=True)
        tmp_dir = args.root / "local" / "tmp_duckdb"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"set memory_limit = '{args.memory_limit}'; set threads = 2; set temp_directory = '{tmp_dir.as_posix()}'"
        )
        if not args.skip_local_build:
            for y in years:
                r = fb.build_vintage(con, args.root, y)
                bad = [t for t in r.tie_outs if not t.ok]
                print(f"  PC {y}: {sum(r.rows_by_split.values()):,} training rows, {r.horizon_rows:,} horizon rows, "
                      f"tie-outs {'ok' if not bad else bad}")  # fmt: skip

        name, remote = newest_result()
        if remote["vintages"] != years:
            raise ValueError(f"the Databricks result covers {remote['vintages']}, not {years}")
        report: dict[str, Any] = {"result_file": name}
        with tempfile.TemporaryDirectory() as tmp:
            download(
                remote["export"].replace("/Volumes/", "dbfs:/Volumes/"), Path(tmp) / "slice", recursive=True
            )
            for kind, table in (("features", "features"), ("horizon", "features_horizon")):
                local_files = [p.as_posix() for base in (table, f"{table}_holdout")
                               for p in (args.root / "local" / base).rglob("vintage=*.parquet")
                               if int(p.stem.split("=")[1]) in years]  # fmt: skip
                remote_glob = (Path(tmp) / "slice" / kind).as_posix() + "/*/*.parquet"
                cur = con.execute(
                    compare_sql(fb.parquet_relation(local_files), f"read_parquet('{remote_glob}')")
                )
                row = dict(zip([d[0] for d in cur.description], cur.fetchall()[0], strict=True))
                report[kind] = row
                differing = {c: row[c] for c in COLUMNS if row[c]}
                print(
                    f"  {kind}: {row['matched']:,} rows matched; only on the PC {row['only_pc']:,}; "
                    f"only on Databricks {row['only_databricks']:,}; columns differing: {differing or 'none'}"
                )

        # Economic tables: Databricks' row counts and value sums against the PC's silver files.
        job_run = latest_run()
        macro = macro_output(job_run)
        report["macro"] = {n: i["rows"] for n, i in macro.items()}
        report["macro_differences"] = macro_differences(con, args.root, macro)
        print(
            f"  economic tables: {report['macro']}; differences from the PC: {report['macro_differences'] or 'none'}"
        )
        # Compute minutes and the projection for 28 vintages, by panel rows.
        minutes = job_minutes(job_run)
        rows = dict(con.execute(
            "select vintage_year, count(*) from read_parquet('" + (args.root / "local" / "panel").as_posix()
            + "/vintage=*/panel.parquet') group by 1").fetchall())  # fmt: skip
        slice_rows = sum(rows[y] for y in years)
        feature_minutes = minutes["minutes_by_task"].get("features", minutes["minutes"])
        projected = feature_minutes * sum(rows.values()) / slice_rows
        report["minutes"] = minutes | {"slice_panel_rows": slice_rows, "all_panel_rows": sum(rows.values()),
                                       "projected_full_feature_minutes": round(projected, 1)}  # fmt: skip
        run.databricks_minutes = minutes["minutes"]
        run.details = report
        print(
            f"Databricks minutes: {minutes['minutes_by_task']} (total {minutes['minutes']}); "
            f"projected full build: about {projected:.0f} minutes for {len(rows)} vintages"
        )
        failed = [k for k in ("features", "horizon") if report[k]["only_pc"] or report[k]["only_databricks"]
                  or any(report[k][c] for c in COLUMNS)]  # fmt: skip
        con.close()
        if report["macro_differences"]:
            failed.append("economic tables")
        if failed:
            raise RuntimeError(f"the PC and Databricks differ in: {failed}")
    print(f"run log: {run_log.log_dir(args.root) / (run.run_id + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
