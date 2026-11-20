"""Load the public economic data on the PC: FRED, FHFA, the Fed's scenarios, then silver.

Same code as the Airflow DAG `cecl_macro_data`. Needs FRED_API_KEY in the environment (from .env;
the key is never printed). Writes under data/ (git-ignored):

    python -m scripts.load_macro                     # everything
    python -m scripts.load_macro --sources fhfa fed  # only some sources (no key needed)
    python -m scripts.load_macro --silver-only       # rebuild silver from the bronze already there
    python -m scripts.load_macro --fed-2025          # the Fed's 2025 scenarios -> silver fed_paths_2025
                                                     # (31 December 2025 reserve; loaded once, no key needed)
    python -m scripts.load_macro --fed-calendar      # 2026 paths on the Fed's quarter dates -> fed_paths_calendar
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from cecl import macro
from cecl.environment import current_env
from cecl.scheduling import TZ
from ingestion import macro_build

# Each loader takes (data root, run date) and returns its summary for the run log.
LOADERS: dict[str, Callable[[Path, Any], dict[str, Any]]] = {
    "fred": macro_build.load_fred,
    "fhfa": macro_build.load_fhfa,
    "fed": macro_build.load_fed,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", default="data", help="data folder (default: data)")
    parser.add_argument("--sources", nargs="+", choices=sorted(LOADERS), default=sorted(LOADERS))
    parser.add_argument("--silver-only", action="store_true")
    parser.add_argument("--fed-2025", action="store_true", help="only the 2025 Fed scenarios and their paths")
    parser.add_argument(
        "--fed-calendar",
        action="store_true",
        help="only the 2026 paths on the Fed's own quarter dates (Phase 5 sensitivity)",
    )
    args = parser.parse_args(argv)

    print("environment:", current_env())  # CECL_ENV is required, as everywhere
    root, run_date = Path(args.root), datetime.now(TZ).date()
    if args.fed_calendar:  # ADR-003 amendment: the first-written timing, kept for the sensitivity table
        out = macro_build.build_fed_paths(
            root, "fed", macro_build.RESERVE_MONTH, timing=macro.CALENDAR, table="fed_paths_calendar"
        )
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.fed_2025:  # point in time: the scenarios a 31 December 2025 reserve would have used
        print(json.dumps(macro_build.load_fed(root, run_date, key="fed_2025"), indent=2, default=str))
        paths = macro_build.build_fed_paths(root, "fed_2025", date(2025, 12, 1))
        print(json.dumps(paths, indent=2, default=str))
        return 0
    results: dict[str, dict[str, Any]] = {}
    if not args.silver_only:
        for source in args.sources:
            results[source] = LOADERS[source](root, run_date)
            print(json.dumps(results[source], indent=2, default=str))
    results["silver"] = macro_build.build_silver(root)
    print(json.dumps(results["silver"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
