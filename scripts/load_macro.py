"""Load the public economic data on the PC: FRED, FHFA, the Fed's scenarios, then silver.

Same code as the Airflow DAG `cecl_macro_data`. Needs FRED_API_KEY in the environment (from .env;
the key is never printed). Writes under data/ (git-ignored):

    python -m scripts.load_macro                     # everything
    python -m scripts.load_macro --sources fhfa fed  # only some sources (no key needed)
    python -m scripts.load_macro --silver-only       # rebuild silver from the bronze already there
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

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
    args = parser.parse_args(argv)

    print("environment:", current_env())  # CECL_ENV is required, as everywhere
    root, run_date = Path(args.root), datetime.now(TZ).date()
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
