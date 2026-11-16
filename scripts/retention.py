"""Raw economic data downloads: list what the retention rule would remove, or remove it.

    python -m scripts.retention            # dry run: lists folders to keep and to remove
    python -m scripts.retention --apply    # deletes the listed folders (asks nothing more)

The rule (ingestion/retention.py): keep the newest 8 downloads of each source, and forever every
download a snapshot manifest lists. Freddie's files are never touched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cecl import environment
from ingestion import retention


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--keep", type=int, default=retention.KEEP_LATEST)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    environment.current_env()

    chosen = retention.plan(args.root, args.keep)
    print(f"keep {len(chosen.keep)} download folders; remove {len(chosen.remove)} "
          f"({chosen.remove_bytes / 1024**2:.1f} MB)")  # fmt: skip
    for rel in chosen.remove:
        print(f"  remove: {rel}")
    if args.apply:
        removed = retention.apply(args.root, chosen)
        print(f"removed {len(removed)} folders")
    elif chosen.remove:
        print("dry run: nothing deleted (add --apply to delete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
