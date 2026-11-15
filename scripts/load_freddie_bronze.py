"""Load Freddie sample zips into local bronze Parquet, and project the size of loading all of them.

Streams from the zips (never unzipped), contract-checks first, keeps 3 GB of disk free, and ties out
row counts (ingestion/freddie.py). Prints counts and sizes only, never loan rows.

    python -m scripts.load_freddie_bronze --years 1999          # load one year, project the rest
    python -m scripts.load_freddie_bronze --years 2000-2026     # load a range
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import zipfile
from pathlib import Path

from ingestion import disk, freddie

DEFAULT_FOLDER = Path("data/raw/freddie/sample")
DEFAULT_OUT = Path("data/local/bronze")
MB = 1024**2


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            years.extend(range(a, b + 1))
        else:
            years.append(int(part))
    return years


def text_size(zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        return sum(i.file_size for i in zf.infolist() if not i.is_dir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Freddie sample zips into bronze Parquet.")
    parser.add_argument("--years", required=True, help="e.g. 1999, or 2000-2026, or 1999,2007")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--release", type=int, default=48)
    args = parser.parse_args(argv)

    zips = {int(re.sub(r"\D", "", z.name)): z for z in sorted(args.folder.glob("sample_*.zip"))}
    wanted = parse_years(args.years)
    missing = [y for y in wanted if y not in zips]
    if missing:
        print(f"no zip for year(s) {missing} in {args.folder}")
        return 2

    print(f"before: {disk.describe(args.out)}")
    loaded_text = loaded_parquet = 0
    for year in wanted:
        started = time.monotonic()
        files = freddie.load_zip(zips[year], args.release, args.out)
        for f in files:
            loaded_text += f.text_bytes
            loaded_parquet += f.parquet_bytes
            print(
                f"  {year} {f.kind}: {f.rows:>10,} rows, text {f.text_bytes / MB:>7.1f} MB -> "
                f"Parquet {f.parquet_bytes / MB:>6.1f} MB ({f.parquet_bytes / f.text_bytes:.1%}), tie-out ok"
            )
        print(f"  {year} done in {time.monotonic() - started:.0f}s")

    ratio = loaded_parquet / loaded_text if loaded_text else freddie.DEFAULT_SIZE_RATIO
    all_text = sum(text_size(z) for z in zips.values())
    print(f"after: {disk.describe(args.out)}")
    print(
        f"\nprojection for all {len(zips)} zips: text {all_text / 1024**3:.2f} GB x measured ratio "
        f"{ratio:.1%} = about {all_text * ratio / 1024**3:.2f} GB of Parquet "
        f"(already written: {loaded_parquet / 1024**3:.2f} GB)"
    )
    remaining = all_text * ratio - loaded_parquet
    left = disk.free_bytes(args.out) - remaining
    print(
        f"free disk after loading the rest: about {left / 1024**3:.1f} GB "
        f"(reserve {disk.RESERVE_BYTES / 1024**3:.0f} GB) -> {'OK' if left >= disk.RESERVE_BYTES else 'NOT ENOUGH'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
