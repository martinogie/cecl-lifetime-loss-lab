"""Quarter-end snapshot of the local data (control C07) and its check (control C08).

    python -m scripts.snapshot create --quarter 2026Q2            # write the manifest
    python -m scripts.snapshot verify --quarter 2026Q2            # compare files on disk with it
    python -m scripts.snapshot tag --quarter 2026Q2               # annotated git tag holding it

`tag` only creates the tag locally; pushing it is a separate, deliberate step
(`git push origin cecl-2026Q2-data-v1`). It refuses if tracked files have uncommitted changes,
because the tag must name the exact code that built the data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from cecl import environment
from ingestion import run_log, snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=("create", "verify", "tag"))
    parser.add_argument("--quarter", required=True, help="e.g. 2026Q2")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    environment.current_env()  # CECL_ENV is required, as everywhere

    name = snapshot.snapshot_name(args.quarter, args.version)
    manifest_path = snapshot.snapshot_dir(args.root, name) / "manifest.json"

    if args.action == "create":
        if manifest_path.exists():
            print(
                f"{name} already exists; a snapshot is never overwritten (use --version {args.version + 1})"
            )
            return 2
        last = run_log.last_success(args.root, "freddie")
        notes = {"last_successful_freddie_run": last["run_id"] if last else None}
        manifest = snapshot.build(args.root, args.quarter, args.version, notes)
        path = snapshot.write(args.root, manifest)
        rows = sum(e.rows or 0 for e in manifest.files)
        print(f"{name}: {len(manifest.files)} files, {rows:,} rows in Parquet files")
        print(
            f"code {manifest.code_commit[:12]} (tree {manifest.code_tree[:12]}), clean: {manifest.code_clean}"
        )
        print(f"fingerprint {manifest.fingerprint()}\nmanifest: {path}")
        return 0

    if not manifest_path.exists():
        print(f"no manifest for {name} at {manifest_path}")
        return 2

    if args.action == "verify":
        result = snapshot.verify(args.root, manifest_path)
        print(f"{name}: {result.matched} files match")
        for label in ("changed", "missing", "extra"):
            for p in getattr(result, label):
                print(f"  {label}: {p}")
        print("verify: OK, the data on disk is exactly the snapshot" if result.ok else "verify: DIFFERENT")
        return 0 if result.ok else 1

    manifest = snapshot.load(manifest_path)
    if snapshot.git("status", "--porcelain", "--untracked-files=no"):
        print("refusing to tag: tracked files have uncommitted changes")
        return 2
    tree_now = snapshot.git("rev-parse", "HEAD^{tree}")
    if tree_now != manifest.code_tree:
        print(f"refusing to tag: HEAD's code tree {tree_now[:12]} is not the tree that built the data "
              f"({manifest.code_tree[:12]})")  # fmt: skip
        return 2
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(snapshot.tag_message(manifest))
    try:
        subprocess.run(["git", "tag", "-a", name, "-F", f.name], check=True)
    finally:
        Path(f.name).unlink()
    print(
        f"created tag {name} on {snapshot.git('rev-parse', '--short=12', 'HEAD')}; push with: git push origin {name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
