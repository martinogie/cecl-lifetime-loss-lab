from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ingestion import data_contracts as dc
from ingestion import disk, freddie

FIXTURES = Path(__file__).with_name("fixtures")
ORIG = FIXTURES / "sample_orig_2023.txt"
PERF = FIXTURES / "sample_perf_2023.txt"
RELEASE = 48


def make_zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


@pytest.fixture
def sample_zip(tmp_path) -> Path:
    return make_zip(
        tmp_path / "sample_2023.zip",
        {"sample_orig_2023.txt": ORIG.read_text(), "sample_perf_2023.txt": PERF.read_text()},
    )


def test_loads_both_files_with_tie_out(sample_zip, tmp_path):
    out = tmp_path / "bronze"
    loaded = freddie.load_zip(sample_zip, RELEASE, out)
    assert [(f.kind, f.vintage, f.rows) for f in loaded] == [("orig", 2023, 3), ("perf", 2023, 6)]
    assert loaded[0].path == out / "freddie_orig" / "vintage=2023" / "sample_orig_2023.parquet"
    assert all(f.path.exists() and f.parquet_bytes > 0 for f in loaded)
    assert not list(out.rglob("*.tmp"))  # no half-written files left


def test_columns_types_and_values(sample_zip, tmp_path):
    loaded = freddie.load_zip(sample_zip, RELEASE, tmp_path / "bronze")
    orig = pq.read_table(loaded[0].path)
    contract = dc.contract_for("orig", RELEASE)
    assert orig.column_names == [*contract.names, "source_file", "release"]
    assert orig.schema.field("credit_score").type == pa.int64()
    assert orig.schema.field("orig_upb").type == freddie.DECIMAL
    assert orig.schema.field("first_payment_date").type == pa.string()
    row = orig.to_pylist()[0]
    assert row["orig_interest_rate"] == Decimal("6.875")  # exact, not 6.87499999...
    assert row["msa"] == "19124"
    assert row["super_conforming_flag"] is None  # empty field -> null
    assert row["source_file"] == "sample_orig_2023.txt" and row["release"] == 48
    # "Not Available" codes stay as Freddie wrote them in bronze (silver turns them into nulls)
    assert orig.to_pylist()[1]["credit_score"] == 9999


def test_perf_codes_kept_as_text(sample_zip, tmp_path):
    loaded = freddie.load_zip(sample_zip, RELEASE, tmp_path / "bronze")
    perf = pq.read_table(loaded[1].path).to_pylist()
    assert perf[0]["delinquency_status"] == "00"  # leading zero kept
    assert perf[5]["zero_balance_code"] == "01"
    assert perf[2]["borrower_assistance_status"] == "F"


def test_rerun_overwrites_the_same_files(sample_zip, tmp_path):
    out = tmp_path / "bronze"
    first = freddie.load_zip(sample_zip, RELEASE, out)
    second = freddie.load_zip(sample_zip, RELEASE, out)
    assert [f.path for f in first] == [f.path for f in second]
    assert len(list(out.rglob("*.parquet"))) == 2


def test_contract_failure_writes_nothing(tmp_path):
    rows = ORIG.read_text().splitlines()
    rows[0] = rows[0].rsplit("|", 1)[0]  # a short row
    z = make_zip(tmp_path / "sample_2023.zip", {"sample_orig_2023.txt": "\n".join(rows) + "\n"})
    out = tmp_path / "bronze"
    with pytest.raises(dc.ContractViolation):
        freddie.load_zip(z, RELEASE, out)
    assert not out.exists()


def test_not_enough_disk_writes_nothing(sample_zip, tmp_path, monkeypatch):
    def full(_path, _needed, **_kw):
        raise disk.NotEnoughDisk("full")

    monkeypatch.setattr(freddie.disk, "check_room", full)
    out = tmp_path / "bronze"
    with pytest.raises(disk.NotEnoughDisk):
        freddie.load_zip(sample_zip, RELEASE, out)
    assert not out.exists()


def test_tie_out_mismatch_removes_output_and_stops(sample_zip, tmp_path, monkeypatch):
    monkeypatch.setattr(
        freddie,
        "_write_member",
        lambda zf, info, contract, path, release: (
            path.parent.mkdir(parents=True, exist_ok=True),
            path.write_bytes(b"x"),
            1,
        )[2],
    )
    out = tmp_path / "bronze"
    with pytest.raises(dc.ContractViolation, match="tie-out failed .* 1 rows written to Parquet, 3 counted"):
        freddie.load_zip(sample_zip, RELEASE, out)
    assert not list(out.rglob("*.parquet"))


def test_empty_file_gives_empty_parquet(tmp_path):
    z = make_zip(tmp_path / "sample_2023.zip", {"sample_orig_2023.txt": ""})
    (loaded,) = freddie.load_zip(z, RELEASE, tmp_path / "bronze")
    assert loaded.rows == 0
    assert pq.read_table(loaded.path).num_rows == 0


def test_vintage_and_paths():
    assert freddie.vintage_of("sample_perf_1999.txt") == 1999
    assert freddie.vintage_of("perf_2007Q3.txt") == 2007
    with pytest.raises(ValueError):
        freddie.vintage_of("readme.txt")
    assert freddie.output_path(Path("b"), "perf", "perf_2007Q3.txt") == Path(
        "b/freddie_perf/vintage=2007/perf_2007Q3.parquet"
    )
