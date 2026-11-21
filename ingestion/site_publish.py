"""Publish the website's data to Cloudflare R2 (private bucket), one run at a time (Phase 7).

The same pattern as Bank Health's publisher, so the site refreshes after a successful prod run without a
rebuild: the finance router serves /cecl/data/* from the run named in current.json.

Layout in the bucket (prod at the root; dev under "dev/", which the site never serves):
    runs/<run key>/data/overview.json, explorer.json, scenarios.json, disclosures.json, methods.json, pipeline.json
    current.json   {"run": "<run key>", "publishedAt", "reserveDate", "files"}   <- the router reads this

Order: build the files (`ingestion/site_export.py`) -> validate each against the site's JSON Schemas ->
scan every file for loan ids and identifiers -> upload to a NEW prefix -> check every object is there ->
switch current.json -> delete runs beyond the newest KEEP (never the current one). A failure before the
switch leaves the live site on the previous run. Rollback = point current.json at an earlier run:

    python -m ingestion.site_publish list
    python -m ingestion.site_publish rollback <run key>

Rules (CLAUDE.md): publishing runs only from CECL_ENV=prod (dev writes under dev/, local never publishes),
and only after every check of the run has passed. Credentials (an R2 token with object read and write on
this bucket only) come from the environment: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT (the CECL
stack's secrets on the VM, .env on the PC; Martins adds them). Values are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cecl import environment

BUCKET = os.environ.get("R2_BUCKET", "cecl-data")
KEEP = 5  # the current run plus the previous 4 publishes
POINTER = "current.json"
RUN_KEY = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
SCHEMA = Path(__file__).resolve().parents[1] / "site" / "data" / "schemas" / "files.schema.json"
DEFS = {"overview.json": "overview", "explorer.json": "explorer", "scenarios.json": "scenarios",
        "disclosures.json": "disclosures", "methods.json": "methods", "pipeline.json": "pipeline"}  # fmt: skip
# Never in a published file: Freddie loan ids, emails, IP addresses, workspace or cloud identifiers, tokens.
FORBIDDEN = {
    "loan id": re.compile(r"\b[FA]\d{2}Q[1-4]\d{7}\b"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}"),
    "IP address": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "workspace or cloud id": re.compile(r"azuredatabricks|cloud\.databricks|ocid1\.|dapi[0-9a-f]{8}", re.I),
}
JSON_TYPE = "application/json; charset=utf-8"


class PublishRefused(RuntimeError):
    """A check failed before anything went live; the site keeps the previous run."""


# ------------------------------------------------------------------------------------------ checks


def validate(docs: Mapping[str, Any]) -> list[str]:
    """Schema problems as 'file: path: message' (empty = valid). Every file must be present."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    registry = Registry().with_resource("files.schema.json", Resource.from_contents(schema))
    problems = [f"{name}: missing" for name in DEFS if name not in docs]
    problems += [f"{name}: not a site data file" for name in docs if name not in DEFS]
    for name, defn in DEFS.items():
        if name not in docs:
            continue
        v = Draft202012Validator({"$ref": f"files.schema.json#/$defs/{defn}"}, registry=registry)
        for err in v.iter_errors(docs[name]):
            loc = "/".join(str(p) for p in err.absolute_path) or "(top)"
            problems.append(f"{name}: {loc}: {err.message[:160]}")
    return problems


def scan(files: Mapping[str, bytes]) -> dict[str, list[str]]:
    """Which forbidden kinds of value each file contains (names of the kinds only, never the values)."""
    hits: dict[str, list[str]] = {}
    for path, data in files.items():
        text = data.decode("utf-8", "replace")
        found = [kind for kind, rx in FORBIDDEN.items() if rx.search(text)]
        if found:
            hits[path] = found
    return hits


def encode(docs: Mapping[str, Any]) -> dict[str, bytes]:
    """data/<file> as compact JSON."""
    return {f"data/{name}": json.dumps(doc, separators=(",", ":")).encode() for name, doc in docs.items()}


def check(docs: Mapping[str, Any], files: Mapping[str, bytes]) -> None:
    problems = validate(docs)
    if problems:
        raise PublishRefused(f"{len(problems)} schema problem(s): " + "; ".join(problems[:10]))
    hits = scan(files)
    if hits:
        raise PublishRefused("identifier scan: " + "; ".join(f"{p}: {', '.join(h)}" for p, h in hits.items()))


# ---------------------------------------------------------------------------------------------- R2


def client() -> Any:
    import boto3
    from botocore.config import Config

    for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT"):
        if not os.environ.get(name):
            raise PublishRefused(f"{name} is not set (the CECL stack's secrets on the VM; .env on the PC)")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def root_for(env_name: str) -> str:
    """Where a run publishes: prod at the root (what the site serves), dev under dev/; local never."""
    if env_name == "prod":
        return ""
    if env_name == "dev":
        return "dev/"
    raise PublishRefused(f"publishing runs only from CECL_ENV=prod (or dev, under dev/), not {env_name}")


def run_key(run_id: str, started_at: datetime) -> str:
    """Sortable, unique key for a run: <started UTC>Z-<6 hex of the run id>."""
    t = (started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)).astimezone(UTC)
    return f"{t:%Y%m%dT%H%M%S}Z-{hashlib.sha256(run_id.encode()).hexdigest()[:6]}"


def _list_keys(s3: Any, prefix: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
        page = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            return keys
        token = page["NextContinuationToken"]


def list_runs(s3: Any, root: str = "") -> list[str]:
    seen = {k[len(root) + 5 :].split("/", 1)[0] for k in _list_keys(s3, f"{root}runs/")}
    return sorted(k for k in seen if RUN_KEY.match(k))


def read_pointer(s3: Any, root: str = "") -> dict[str, Any] | None:
    try:
        return dict(json.loads(s3.get_object(Bucket=BUCKET, Key=f"{root}{POINTER}")["Body"].read()))
    except Exception as exc:  # NoSuchKey on the first publish
        if "NoSuchKey" in type(exc).__name__ or "NoSuchKey" in str(exc) or "404" in str(exc):
            return None
        raise


def write_pointer(s3: Any, root: str, key: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    doc = {"run": key, "publishedAt": datetime.now(UTC).isoformat(timespec="seconds"), **(extra or {})}
    s3.put_object(Bucket=BUCKET, Key=f"{root}{POINTER}", Body=json.dumps(doc).encode(), ContentType=JSON_TYPE,
                  CacheControl="no-cache")  # fmt: skip
    return doc


def upload(s3: Any, root: str, key: str, files: Mapping[str, bytes]) -> int:
    base = f"{root}runs/{key}/"
    for path, data in files.items():
        s3.put_object(Bucket=BUCKET, Key=base + path, Body=data, ContentType=JSON_TYPE,
                      CacheControl="public, max-age=300")  # fmt: skip
    present = set(_list_keys(s3, base))
    missing = [p for p in files if base + p not in present]
    if missing:
        raise PublishRefused(
            f"{len(missing)} object(s) missing after upload, e.g. {missing[:3]}; not switching"
        )
    return len(files)


def prune(s3: Any, root: str, current: str, keep: int = KEEP) -> list[str]:
    runs = list_runs(s3, root)
    old = [k for k in runs[:-keep] if k != current] if len(runs) > keep else []
    for k in old:
        keys = _list_keys(s3, f"{root}runs/{k}/")
        for i in range(0, len(keys), 1000):
            s3.delete_objects(
                Bucket=BUCKET, Delete={"Objects": [{"Key": x} for x in keys[i : i + 1000]], "Quiet": True}
            )
    return old


def publish(s3: Any, root: str, key: str, docs: Mapping[str, Any]) -> dict[str, Any]:
    """Check, upload, switch the pointer, prune. Returns a summary for the run log (no secrets)."""
    files = encode(docs)
    check(docs, files)
    previous = (read_pointer(s3, root) or {}).get("run")
    n = upload(s3, root, key, files)
    reserve_date = docs["overview.json"]["dataAsOf"]["reserveDate"]
    pointer = write_pointer(s3, root, key, {"reserveDate": reserve_date, "files": n})
    deleted = prune(s3, root, key)
    return {"run": key, "files": n, "previous": previous, "deleted": deleted, "pointer": pointer}


def rollback(s3: Any, root: str, to: str) -> dict[str, Any]:
    if to not in list_runs(s3, root):
        raise PublishRefused(f"run {to} is not in the bucket (python -m ingestion.site_publish list)")
    current = read_pointer(s3, root) or {}
    return write_pointer(
        s3, root, to, {"rolledBackFrom": current.get("run"), "reserveDate": current.get("reserveDate")}
    )


def run(data_root: Path, run_id: str, started_at: datetime, s3: Any = None) -> dict[str, Any]:
    """The publish step at the end of a successful run: build the files, then publish them."""
    from ingestion import site_export

    root = root_for(environment.current_env())
    docs = site_export.build(data_root)
    return publish(s3 or client(), root, run_key(run_id, started_at), docs)


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="List the published runs or roll the site back to one.")
    p.add_argument("--dev", action="store_true", help="the dev/ prefix instead of prod")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    rb = sub.add_parser("rollback")
    rb.add_argument("run")
    args = p.parse_args(list(argv) if argv is not None else None)
    root = "dev/" if args.dev else ""
    s3 = client()
    current = (read_pointer(s3, root) or {}).get("run")
    if args.cmd == "list":
        for k in list_runs(s3, root):
            print(k, "<- current" if k == current else "")
        return 0
    doc = rollback(s3, root, args.run)
    print(f"current.json now points at {doc['run']} (was {doc.get('rolledBackFrom')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
