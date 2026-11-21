"""Load the FFIEC call reports (Schedule RI-C) and write the peer-ratio aggregates (Phase 6).

    CECL_ENV=local python -m scripts.load_ffiec              # the initial periods + the latest quarter
    CECL_ENV=local python -m scripts.load_ffiec --periods 2026-06-30

Same code as the Airflow DAG `cecl_ffiec_call_reports`. Needs FFIEC_PWS_USERNAME and FFIEC_PWS_TOKEN
(Martins adds them to .env; this script copies only those keys into its own environment and never shows
them). Resumable: each bank-period is cached under data/raw/ffiec/ (git-ignored), so a stopped run picks
up where it left off. Refuses to start in the blocked windows (Monday before 13:00, 02:30-04:00).
Writes the aggregates only to docs/phase6/peer_ratios.json (no bank-level data leaves the PC).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from cecl import environment, scheduling
from ingestion import ffiec, run_log

OUT = Path("docs/phase6/peer_ratios.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", default="data")
    parser.add_argument(
        "--periods", nargs="*", default=None, help="YYYY-MM-DD quarter-ends (default: initial + latest)"
    )
    parser.add_argument("--no-write", action="store_true", help="do not write the aggregates file")
    args = parser.parse_args(argv)
    print("environment:", environment.current_env())
    scheduling.refuse_blocked_window()
    ffiec.load_env_file()
    today = datetime.now(scheduling.TZ).date()
    contract = ffiec.load_contract()
    periods = ([date.fromisoformat(p) for p in args.periods] if args.periods
               else ffiec.periods_from(contract, [ffiec.latest_quarter_end(today)]))  # fmt: skip
    root = Path(args.root)
    with run_log.logged_run(root, "ffiec", periods=[p.isoformat() for p in periods]) as run:
        summary = ffiec.run(root, periods, today)
        run.rows.update({f"ffiec_ok_{p}": c["ok"] for p, c in summary["periods"].items()})
        run.contracts.append(
            {"file": "ffiec_call", "contract": "pass", "checked": summary["bank_periods_ok"]}
        )
        run.details = {"token": summary["token"], "api_calls": summary["api_calls"]}
    print(json.dumps({k: summary[k] for k in ("token", "periods", "bank_periods_ok", "api_calls")}, indent=1))
    if not args.no_write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "note": "Aggregates only: residential real estate allowance / residential real estate loans (FFIEC call "
                    "reports, Schedule RI-C item 1.c, as each bank segments its allowance), institutions with $1bn+ "
                    "in assets. A sanity range for an educational model, not a target.",
            "source": "FFIEC Central Data Repository, Public Web Service (RetrievePanelOfReporters, RetrieveFacsimile); "
                      "total assets for the RI-C filer test from the FDIC BankFind API",
            "periods": summary["periods"], "aggregates": summary["aggregates"]}, indent=1), encoding="utf-8")  # fmt: skip
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
