"""Seed the closes the live quarterly close starts from (Phase 8, once per environment).

    CECL_ENV=prod python -m scripts.seed_close            # dry run: builds and checks, uploads nothing
    CECL_ENV=prod python -m scripts.seed_close --yes      # uploads to the environment's volume

The prod close job (notebook 68) rolls forward from the previous close's booked frame. The first live close
(30 September 2026) therefore needs the booked closes of Phase 4 in the same form: for 31 December 2025,
31 March and 30 June 2026, from the PC's priced files (`data/local/reserve/priced_*`, the files that booked
the reserve) and the committed results (`docs/phase4/reserve_2026Q2.json`):

- `results/close/<ym>/priced_<ym>_<ym>_<scenario>.parquet` (loan level; private volume, never git);
- `results/close/<ym>/booked.parquet` (each loan's booked reserve and group: the next rollforward's start);
- `results/close/<ym>/close.json` (that date's committed aggregates, and its rollforward where one exists);
- `models/phase3/lgd_model.pkl` and `ead_rates.json` (the Phase 3 LGD model and EAD rates).

Checks before anything is uploaded: each booked frame adds up to the committed total allowance (to the cent),
and the 30 June frame's rollforward end matches. An existing close in the volume is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cecl import close, environment, scenarios
from scripts import build_reserve as br

PROFILE = "cecl"
COMMITTED = Path("docs/phase4/reserve_2026Q2.json")
SEED = ("202512", "202603", "202606")
TAGS = {"202603": "rollforward_q1_2026", "202606": "rollforward_q2_2026"}


def volume() -> str:
    return "dbfs:/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"


def exists(remote: str) -> bool:
    out = subprocess.run(
        ["databricks", "fs", "ls", remote, "--profile", PROFILE], capture_output=True, text=True
    )
    return out.returncode == 0


def upload(local: Path, remote: str) -> None:
    subprocess.run(["databricks", "fs", "cp", str(local), remote, "--profile", PROFILE, "--overwrite"],
                   check=True, capture_output=True)  # fmt: skip


def record(res: dict[str, Any], ym: str, commit: str) -> dict[str, Any]:
    """The seeded close's record: the committed aggregates for that date (and its rollforward)."""
    return {"note": res["note"], "model": res["model"], "reserve_ym": ym,
            "prior_ym": close.prior_quarter(ym), "dates": {ym: res["dates"][ym]},
            "rollforward": res.get(TAGS.get(ym, ""), None), "seeded_from": str(COMMITTED).replace("\\", "/"),
            "snapshot": {"code_version": commit}}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", action="store_true", help="upload (otherwise only build and check)")
    args = parser.parse_args(argv)
    env = environment.require_airflow_env().name
    import duckdb
    import pandas as pd

    res = json.loads(COMMITTED.read_text(encoding="utf-8"))
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    con = duckdb.connect()
    inp = br.load_inputs(con)
    orig_year = con.sql("select loan_id, cast(substr(first_payment_date, 1, 4) as int) as vintage_year from "
                        f"{br.REL['orig']}").df()  # fmt: skip
    vol = volume()
    plan: list[tuple[Path, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for ym in SEED:
            frames = {
                s: pd.read_parquet(br.RES / f"priced_{ym}_{ym}_{s}.parquet") for s in scenarios.SCENARIOS
            }
            pipe = close.defaulted_pipeline(con, br.REL, ym, inp["lgd"])
            d = res["dates"][ym]
            booked = close.booked_frame(frames, pipe, orig_year, d["overlays"]["booked_total"])
            total = float(booked["reserve"].sum())
            if abs(total - d["total_allowance"]) >= 0.01:
                raise SystemExit(f"{ym}: booked frame {total:,.2f} != committed {d['total_allowance']:,.2f}")
            rf = res.get(TAGS.get(ym, ""))
            if rf and str(close.cents(total)) != rf["total"]["end"]:
                raise SystemExit(
                    f"{ym}: booked frame {close.cents(total)} != rollforward end {rf['total']['end']}"
                )
            print(f"{ym}: booked {total:,.2f} = committed total allowance; {len(booked):,} rows")
            folder = Path(tmp) / ym
            folder.mkdir()
            booked.to_parquet(folder / "booked.parquet", index=False)
            (folder / "close.json").write_text(json.dumps(record(res, ym, commit), indent=1, default=float))
            remote = f"{vol}/results/close/{ym}"
            if exists(f"{remote}/close.json"):
                print(f"  {remote} already seeded: left as is")
                continue
            plan += [(br.RES / f"priced_{ym}_{ym}_{s}.parquet", f"{remote}/priced_{ym}_{ym}_{s}.parquet")
                     for s in scenarios.SCENARIOS]  # fmt: skip
            plan += [
                (folder / "booked.parquet", f"{remote}/booked.parquet"),
                (folder / "close.json", f"{remote}/close.json"),
            ]
        for name in ("lgd_model.pkl", "ead_rates.json"):
            plan.append((br.LOCAL / "phase3" / name, f"{vol}/models/phase3/{name}"))
        for local, remote in plan:
            print(f"{'upload' if args.yes else 'would upload'} {local.name} -> {remote}")
            if args.yes:
                upload(local, remote)
    if not args.yes:
        print(f"dry run ({env}): nothing uploaded (add --yes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
