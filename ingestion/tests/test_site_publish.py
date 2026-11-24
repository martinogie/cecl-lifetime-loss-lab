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


def test_a_successful_close_becomes_the_site_headline(tmp_path: Path) -> None:
    """ADR-012 decision: after a close, the site shows the new quarter (newest date, its rollforward and bridge,
    its disclosures and explorer groups); a close whose controls failed is never published."""
    import copy
    import json

    from ingestion import site_export

    base = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/phase4/reserve_2026Q2.json").read_text(encoding="utf-8")
    )
    groups = json.loads(
        (Path(__file__).resolve().parents[2] / site_export.BY_GROUP).read_text(encoding="utf-8")
    )
    d = copy.deepcopy(base["dates"]["202606"])
    d["total_allowance"] = 301_234_567.89
    disc = {k: v for k, v in base["disclosures"].items() if "gross_write_offs" not in k}
    disc["gross_write_offs_by_origination_year_q3_2026"] = {"2023": 400_000.0}
    disc["gross_write_offs_by_origination_year_ytd_2026"] = {"2023": 1_300_000.0}
    rec = {"reserve_ym": "202609", "prior_ym": "202606", "dates": {"202606": base["dates"]["202606"], "202609": d},
           "rollforward": base["rollforward_q2_2026"], "bridge": base["bridge_q1_to_q2_2026"], "disclosures": disc,
           "benchmarks": base["benchmarks"], "controls": {"all_pass": True},
           "site_explorer": {k: v for k, v in groups.items() if k not in ("dataAsOf", "status")}}  # fmt: skip
    (tmp_path / "close" / "202609").mkdir(parents=True)
    (tmp_path / "close" / "202609" / "close.json").write_text(json.dumps(rec))
    failed = rec | {"reserve_ym": "202612", "prior_ym": "202609", "controls": {"all_pass": False}}
    (tmp_path / "close" / "202612").mkdir()
    (tmp_path / "close" / "202612" / "close.json").write_text(json.dumps(failed))
    try:
        docs = site_export.build(tmp_path)
        assert sp.validate(docs) == []
        o, x = docs["overview.json"], docs["disclosures.json"]
        assert o["latest"] == "202609" and o["dateOrder"] == ["202512", "202603", "202606", "202609"]
        assert (
            o["dates"]["202609"]["totalM"] == 301.235
            and o["dates"]["202609"]["longLabel"] == "30 September 2026"
        )
        assert (
            o["dataAsOf"]["reserveDate"] == "2026-09-30"
            and docs["methods.json"]["dataAsOf"]["reserveDate"] == "2026-09-30"
        )
        assert [q["key"] for q in x["quarters"]] == ["q1_2026", "q2_2026", "q3_2026"]
        assert x["quarters"][-1]["bridgeTitle"] == "30 Jun to 30 Sep 2026" and x["quarterLabel"] == "Q3 2026"
        assert x["writeOffsQuarter"] == {"2023": 0.4} and x["ytdLabel"] == "January - September 2026"
        assert x["creditQualityDate"] == "30 September 2026" and "202612" not in o["dates"]
        assert docs["explorer.json"]["dataAsOf"]["reserveDate"] == "2026-09-30"
    finally:
        site_export.build(tmp_path / "none")  # reset the module's reserve date for other tests
    assert site_export.span("202512", "202603") == "31 Dec 2025 to 31 Mar 2026"
