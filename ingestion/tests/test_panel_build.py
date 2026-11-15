from __future__ import annotations

from decimal import Decimal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ingestion import panel_build as pb


def record(loan: str, period: str, status: str, **extra) -> dict:
    base = {
        "loan_id": loan,
        "vintage_year": 2020,
        "period_ym": period,
        "loan_age": 1,
        "current_upb": Decimal("100000.00"),
        "remaining_months": 359,
        "term_over_360": False,
        "delinquency_status": status,
        "zero_balance_code": None,
        "modification_flag": None,
        "payment_deferral_flag": None,
        "borrower_assistance_status": None,
        "delinquency_due_to_disaster": None,
    }
    return {**base, **extra}


RECORDS = [
    # loan A: prepays in month 3
    record("A", "202001", "00"),
    record("A", "202002", "00"),
    record("A", "202003", "00", zero_balance_code="01"),
    # loan B: defaults at 90 days, then more months that must be dropped (first default only)
    record("B", "202001", "01"),
    record("B", "202002", "02"),
    record("B", "202003", "03"),
    record("B", "202004", "04"),
    # loan C: long term, still alive at data end
    record("C", "202001", "00", term_over_360=True),
    record("C", "202002", "00", term_over_360=True),
]
NEW_LOAN = [record("D", "202605", "XX", loan_age=0), record("D", "202606", "00", loan_age=1)]
SCHEMA = pa.schema(
    [
        ("loan_id", pa.string()),
        ("vintage_year", pa.int32()),
        ("period_ym", pa.string()),
        ("loan_age", pa.int32()),
        ("current_upb", pa.decimal128(18, 2)),
        ("remaining_months", pa.int32()),
        ("term_over_360", pa.bool_()),
        *[(c, pa.string()) for c in pb.SOURCE_COLUMNS[7:]],
    ]
)


def batches(records: list[dict], size: int) -> list[pa.RecordBatch]:
    table = pa.Table.from_pylist(records, schema=SCHEMA)
    return table.to_batches(max_chunksize=size)


@pytest.mark.parametrize("size", [1, 2, 4, 100])  # loans split across batch edges must still group
def test_grouping_across_batches(size):
    loans = list(pb.loans_from_batches(batches(RECORDS, size)))
    assert [(g[0]["loan_id"], len(g)) for g in loans] == [("A", 3), ("B", 4), ("C", 2)]


def test_build_vintage_writes_panel_and_summary(tmp_path):
    out = tmp_path / "vintage=2020" / "panel.parquet"
    s = pb.build_vintage(batches(RECORDS, 2), 2020, loans_in_silver=3, out_path=out)
    assert (s.loans, s.loan_months) == (3, 3 + 3 + 2)  # B keeps 3 rows: up to its default
    assert s.final_status == {"prepaid": 1, "defaulted": 1, "censored": 1}
    assert s.exits == {"prepaid": 1, "default_90_days": 1, "censored_data_end": 1}
    assert (s.loans_term_over_360, s.final_status_term_over_360) == (1, {"censored": 1})
    assert s.loans_xx_not_yet_due == 0
    table = pq.read_table(out)
    assert table.schema == pb.PANEL_SCHEMA
    assert table.num_rows == 8
    assert table.column("current_upb").to_pylist()[0] == Decimal("100000.00")
    assert s.as_dict()["final_status"] == {"prepaid": 1, "defaulted": 1, "censored": 1}
    assert not list(tmp_path.rglob("*.tmp"))


def test_loan_count_mismatch_stops_and_keeps_nothing(tmp_path):
    out = tmp_path / "vintage=2020" / "panel.parquet"
    with pytest.raises(pb.PanelTieOutError, match="3 loans in the panel, 4 in silver"):
        pb.build_vintage(batches(RECORDS, 100), 2020, loans_in_silver=4, out_path=out)
    assert not out.exists() and not list(tmp_path.rglob("*.tmp"))


def test_a_loan_without_exactly_one_final_row_stops(tmp_path, monkeypatch):
    real = pb.panel_records

    def broken(records):
        rows = real(records)
        rows[-1] = {**rows[-1], "status": "current"}  # no final row
        return rows

    monkeypatch.setattr(pb, "panel_records", broken)
    with pytest.raises(pb.PanelTieOutError, match="0 final rows"):
        pb.build_vintage(batches(RECORDS, 100), 2020, 3, tmp_path / "p.parquet")


def test_build_from_duckdb(tmp_path):
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create schema silver")
    records_table = pa.Table.from_pylist(RECORDS, schema=SCHEMA)  # noqa: F841 (read by DuckDB below)
    con.execute(
        "create table silver.int_freddie__loan_months as "
        "select *, cast(substr(period_ym,1,4) as int)*12 + cast(substr(period_ym,5,2) as int) as period_index from records_table"
    )
    con.close()
    s = pb.build_from_duckdb(db, 2020, tmp_path / "panel")
    assert s.loans == 3 and s.loans_in_silver == 3
    assert (tmp_path / "panel" / "vintage=2020" / "panel.parquet").exists()


def test_new_loan_with_xx_at_age_0_is_counted_in_the_run_log(tmp_path):
    s = pb.build_vintage(batches(NEW_LOAN, 100), 2026, 1, tmp_path / "p.parquet")
    assert s.loans_xx_not_yet_due == 1
    assert s.exits == {"censored_data_end": 1}
    first = pq.read_table(tmp_path / "p.parquet").to_pylist()[0]
    assert first["status_not_yet_due"] and first["status"] == "current"
