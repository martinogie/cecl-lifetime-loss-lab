"""Site publisher: made-up files and a fake bucket only (no network, no R2)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("jsonschema")

from ingestion import site_publish as sp  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "site" / "public" / "data"


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_after: int | None = None

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kw: Any) -> None:
        if self.fail_after is not None and Key.endswith(".json") and "runs/" in Key:
            if self.fail_after == 0:
                return  # silently lost: the check after upload must catch it
            self.fail_after -= 1
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise KeyError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket: str, Prefix: str, **kw: Any) -> dict[str, Any]:
        return {
            "Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)],
            "IsTruncated": False,
        }

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> None:
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


def docs() -> dict[str, Any]:
    return {name: json.loads((DATA / name).read_text(encoding="utf-8")) for name in sp.DEFS}


def key(i: int) -> str:
    return sp.run_key(f"run-{i}", datetime(2026, 11, 21, 3, 0, i, tzinfo=UTC))


def test_publish_switches_the_pointer_and_keeps_five_runs() -> None:
    s3 = FakeS3()
    for i in range(7):
        out = sp.publish(s3, "", key(i), docs())
    assert out["files"] == 6 and out["previous"] == key(5)
    assert json.loads(s3.objects["current.json"])["run"] == key(6)
    assert sp.list_runs(s3) == [key(i) for i in range(2, 7)]  # the newest five
    assert f"runs/{key(6)}/data/overview.json" in s3.objects


def test_a_bad_file_never_goes_live() -> None:
    s3 = FakeS3()
    sp.publish(s3, "", key(1), docs())
    bad = docs()
    del bad["overview.json"]["status"]
    with pytest.raises(sp.PublishRefused, match="schema"):
        sp.publish(s3, "", key(2), bad)
    leaky = docs()
    leaky["pipeline.json"]["steps"][0]["what"] += " F99Q10521938"  # a made-up loan id
    with pytest.raises(sp.PublishRefused, match="loan id"):
        sp.publish(s3, "", key(3), leaky)
    assert json.loads(s3.objects["current.json"])["run"] == key(1)  # still the previous run


def test_a_lost_upload_stops_before_the_switch() -> None:
    s3 = FakeS3()
    sp.publish(s3, "", key(1), docs())
    s3.fail_after = 3
    with pytest.raises(sp.PublishRefused, match="missing after upload"):
        sp.publish(s3, "", key(2), docs())
    assert json.loads(s3.objects["current.json"])["run"] == key(1)


def test_rollback_and_environments() -> None:
    s3 = FakeS3()
    sp.publish(s3, "", key(1), docs())
    sp.publish(s3, "", key(2), docs())
    doc = sp.rollback(s3, "", key(1))
    assert doc["run"] == key(1) and doc["rolledBackFrom"] == key(2)
    with pytest.raises(sp.PublishRefused):
        sp.rollback(s3, "", "20990101T000000Z-abcdef")
    assert sp.root_for("prod") == "" and sp.root_for("dev") == "dev/"
    with pytest.raises(sp.PublishRefused, match="CECL_ENV=prod"):
        sp.root_for("local")


def test_credentials_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    pytest.importorskip("boto3")
    with pytest.raises(sp.PublishRefused, match="R2_ACCESS_KEY_ID is not set"):
        sp.client()


def test_build_needs_no_loan_level_files(tmp_path: Path) -> None:
    """On the VM there are no priced loan files: the explorer comes from the saved group totals."""
    from ingestion import site_export

    docs = site_export.build(tmp_path / "no-data-here")
    assert set(docs) == set(sp.DEFS) and sp.validate(docs) == []
    assert docs["explorer.json"]["totals"]["loans"] > 0


def test_written_date_is_chicago_not_utc() -> None:
    # 22 Nov 2026 21:30 Chicago is already 23 Nov in UTC (the VM's containers): the site must say 22 Nov.
    from datetime import UTC, datetime

    from ingestion import site_export

    assert site_export.written_on(datetime(2026, 11, 23, 3, 30, tzinfo=UTC)) == "2026-11-22"
    assert site_export.written_on(datetime(2026, 11, 23, 15, 0, tzinfo=UTC)) == "2026-11-23"
