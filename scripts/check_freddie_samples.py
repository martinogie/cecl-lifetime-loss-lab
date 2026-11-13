"""Read-only data-contract check of Freddie's sample zips (Phase 0).

For each sample_YYYY.zip: streams sample_orig_YYYY.txt and sample_perf_YYYY.txt straight out of the
zip (nothing is extracted: the files total about 8.8 GB and the PC has less free disk than that),
checks them against the contracts for the given release, and reports pass/fail, row counts,
distinct loans and any findings. Prints counts and findings only, never loan rows. Writes nothing
except a small JSON summary under data/ (git-ignored).

    python -m scripts.check_freddie_samples [folder] [--release 48] [--workers 3]   (from the repo root)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ingestion import data_contracts as dc

DEFAULT_FOLDER = Path("data/raw/freddie/sample")
SUMMARY = Path("data/local/contract_check_samples.json")


def check_one(zip_path: str, release: int) -> dict[str, Any]:
    """Check one zip; return counts and findings (no loan values)."""
    started = time.monotonic()
    results = dc.check_zip(Path(zip_path), release)
    counts: dict[str, dict[str, int]] = {}
    for r in results:
        member = r.file.split(":", 1)[1]
        try:
            counts[dc.kind_for(Path(member))] = {
                "rows": r.rows,
                "loans": r.distinct_ids,
                **{f"empty_{k}": v for k, v in r.tolerated_empty.items()},
                **{f"flag_{k}": v for k, v in r.flagged.items()},
            }
        except dc.ContractViolation:
            pass  # an unexpected member: check_zip already recorded it as an error
    missing = [k for k in ("orig", "perf") if k not in counts]
    errors = [e for r in results for e in r.errors] + [f"{Path(zip_path).name}: no {k} file" for k in missing]
    # Loans in the origination file with no monthly rows yet (recent loans near the cut-off).
    # Reported, never an error: the Phase 1 tie-out allows and reports them (L-015).
    no_monthly = counts.get("orig", {}).get("loans", 0) - counts.get("perf", {}).get("loans", 0)
    return {
        "zip": Path(zip_path).name,
        "members": [r.file.split(":", 1)[1] for r in results],
        "ok": not errors,
        "loans_without_monthly_rows": no_monthly,
        "counts": counts,
        "errors": errors,
        "warnings": [w for r in results for w in r.warnings],
        "seconds": round(time.monotonic() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only contract check of Freddie's sample zips.")
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--release", type=int, default=48)
    parser.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    args = parser.parse_args(argv)

    zips = sorted(args.folder.glob("sample_*.zip"))
    if not zips:
        print(f"no sample_*.zip files in {args.folder}")
        return 2
    print(
        f"{len(zips)} zips, release {args.release}, {args.workers} workers; streaming from the zips, no extraction"
    )
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(check_one, [str(z) for z in zips], [args.release] * len(zips)))

    # Run-log columns: rm empty = perf rows with remaining_months empty (tolerated up to 0.1%, L-013);
    # term>360 = origination loans with a term over 360 months (flag, L-014); no monthly = loans with
    # no monthly rows yet (L-015).
    print(
        f"\n{'year':6s} {'result':6s} {'orig rows':>10s} {'perf rows':>12s} {'perf loans':>10s} "
        f"{'rm empty':>8s} {'term>360':>8s} {'no monthly':>10s} {'secs':>6s}"
    )
    for row in rows:
        o = row["counts"].get("orig", {})
        p = row["counts"].get("perf", {})
        year = re.sub(r"\D", "", row["zip"])  # outside the f-string: Python 3.11 allows no backslash inside
        print(
            f"{year:6s} {'PASS' if row['ok'] else 'FAIL':6s} {o.get('rows', 0):>10,} "
            f"{p.get('rows', 0):>12,} {p.get('loans', 0):>10,} {p.get('empty_remaining_months', 0):>8,} "
            f"{o.get('flag_orig_loan_term>360', 0):>8,} {row['loans_without_monthly_rows']:>10,} {row['seconds']:>6}"
        )
    total_o = sum(r["counts"].get("orig", {}).get("rows", 0) for r in rows)
    total_p = sum(r["counts"].get("perf", {}).get("rows", 0) for r in rows)
    print(f"{'total':6s} {'':6s} {total_o:>10,} {total_p:>12,}   ({time.monotonic() - started:.0f}s elapsed)")

    findings = [
        (lvl, msg) for r in rows for lvl, key in (("ERROR", "errors"), ("WARN", "warnings")) for msg in r[key]
    ]
    print()
    for lvl, msg in findings:
        print(f"{lvl} {msg}")
    if not findings:
        print("no findings")

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps({"release": args.release, "zips": rows}, indent=1), encoding="utf-8")
    print(f"\nsummary written to {SUMMARY} (git-ignored; counts and findings only)")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
