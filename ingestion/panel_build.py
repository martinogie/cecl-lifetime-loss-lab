"""Build the monthly loan panel from silver, one vintage at a time (Phase 1, step 4).

Reads silver.int_freddie__loan_months from the local DuckDB database (built by dbt), streams each
vintage's records in loan and month order, applies the ADR-002 rules (cecl.panel.build_loan_panel)
to one loan at a time, and writes the panel to Parquet: <out_root>/vintage=<year>/panel.parquet.

Memory stays flat: records arrive in batches, and only one loan is held at a time (a loan that
straddles two batches is carried over).

Tie-outs (control C02), checked before the file is kept; a failure stops the build:
- every loan in silver for the vintage appears in the panel;
- every loan ends with exactly one final row (defaulted, prepaid or censored), and it is its last row.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from cecl.panel import LoanMonth, PanelRow, build_loan_panel

FINAL = ("defaulted", "prepaid", "censored")
BATCH_ROWS = 200_000

SOURCE_COLUMNS = [
    "loan_id",
    "vintage_year",
    "period_ym",
    "loan_age",
    "current_upb",
    "remaining_months",
    "term_over_360",
    "delinquency_status",
    "zero_balance_code",
    "modification_flag",
    "payment_deferral_flag",
    "borrower_assistance_status",
    "delinquency_due_to_disaster",
]
QUERY = (
    f"select {', '.join(SOURCE_COLUMNS)} from silver.int_freddie__loan_months "
    "where vintage_year = ? order by loan_id, period_index"
)

PANEL_SCHEMA = pa.schema(
    [
        ("loan_id", pa.string()),
        ("vintage_year", pa.int32()),
        ("period_ym", pa.string()),
        ("loan_age", pa.int32()),
        ("current_upb", pa.decimal128(18, 2)),
        ("remaining_months", pa.int32()),
        ("term_over_360", pa.bool_()),
        ("status", pa.string()),
        ("exit_reason", pa.string()),
        ("reported_months_late", pa.int32()),
        ("effective_months_late", pa.int32()),
        ("flagged_pause", pa.bool_()),
        ("status_carried_forward", pa.bool_()),
        ("status_not_yet_due", pa.bool_()),
    ]
)


class PanelTieOutError(RuntimeError):
    """The panel doesn't tie out to silver; the vintage's output is not kept."""


@dataclass
class VintageSummary:
    vintage: int
    loans_in_silver: int = 0
    loans: int = 0
    loan_months: int = 0
    exits: Counter[str] = field(default_factory=Counter)  # exit_reason -> loans
    final_status: Counter[str] = field(default_factory=Counter)  # defaulted / prepaid / censored
    final_status_term_over_360: Counter[str] = field(default_factory=Counter)
    loans_term_over_360: int = 0
    # Run log: loans whose first month was XX at loan age 0, read as "not yet due" (ADR-002 E clarification)
    loans_xx_not_yet_due: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "vintage": self.vintage,
            "loans_in_silver": self.loans_in_silver,
            "loans": self.loans,
            "loan_months": self.loan_months,
            "final_status": dict(self.final_status),
            "exits": dict(self.exits),
            "loans_term_over_360": self.loans_term_over_360,
            "final_status_term_over_360": dict(self.final_status_term_over_360),
            "loans_xx_not_yet_due": self.loans_xx_not_yet_due,
        }


def loans_from_batches(batches: Iterable[pa.RecordBatch]) -> Iterator[list[dict[str, Any]]]:
    """Group records (already in loan, month order) into one list per loan, across batch edges."""
    current: list[dict[str, Any]] = []
    for batch in batches:
        for record in batch.to_pylist():
            if current and record["loan_id"] != current[0]["loan_id"]:
                yield current
                current = []
            current.append(record)
    if current:
        yield current


def to_month(record: dict[str, Any]) -> LoanMonth:
    return LoanMonth(
        period=int(record["period_ym"]),
        delinquency_status=record["delinquency_status"],
        zero_balance_code=record["zero_balance_code"],
        modification_flag=record["modification_flag"],
        payment_deferral_flag=record["payment_deferral_flag"],
        borrower_assistance_status=record["borrower_assistance_status"],
        delinquency_due_to_disaster=record["delinquency_due_to_disaster"],
        loan_age=record["loan_age"],
    )


def panel_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One loan's panel rows, joined back to the record fields carried into the panel."""
    rows: list[PanelRow] = build_loan_panel([to_month(r) for r in records])
    out = []
    for record, row in zip(records, rows, strict=False):  # rows stop at the loan's exit
        out.append(
            {
                "loan_id": record["loan_id"],
                "vintage_year": record["vintage_year"],
                "period_ym": record["period_ym"],
                "loan_age": record["loan_age"],
                "current_upb": record["current_upb"],
                "remaining_months": record["remaining_months"],
                "term_over_360": record["term_over_360"],
                "status": row.status,
                "exit_reason": row.exit_reason,
                "reported_months_late": row.reported_months_late,
                "effective_months_late": row.effective_months_late,
                "flagged_pause": row.flagged_pause,
                "status_carried_forward": row.status_carried_forward,
                "status_not_yet_due": row.status_not_yet_due,
            }
        )
    return out


def build_vintage(
    batches: Iterable[pa.RecordBatch], vintage: int, loans_in_silver: int, out_path: Path
) -> VintageSummary:
    """Build one vintage's panel from its record batches; tie out; write Parquet atomically."""
    summary = VintageSummary(vintage=vintage, loans_in_silver=loans_in_silver)
    tmp = out_path.with_suffix(".parquet.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(tmp, PANEL_SCHEMA, compression="zstd")
    buffer: list[dict[str, Any]] = []
    try:
        for records in loans_from_batches(batches):
            rows = panel_records(records)
            finals = [r for r in rows if r["status"] in FINAL]
            if len(finals) != 1 or rows[-1]["status"] not in FINAL:
                raise PanelTieOutError(
                    f"vintage {vintage}: a loan ended with {len(finals)} final rows (must be exactly 1, last)"
                )
            final = rows[-1]
            summary.loans += 1
            summary.loan_months += len(rows)
            summary.final_status[final["status"]] += 1
            summary.exits[final["exit_reason"]] += 1
            if any(r["status_not_yet_due"] for r in rows):
                summary.loans_xx_not_yet_due += 1
            if final["term_over_360"]:
                summary.loans_term_over_360 += 1
                summary.final_status_term_over_360[final["status"]] += 1
            buffer.extend(rows)
            if len(buffer) >= BATCH_ROWS:
                writer.write_table(pa.Table.from_pylist(buffer, schema=PANEL_SCHEMA))
                buffer = []
        if buffer:
            writer.write_table(pa.Table.from_pylist(buffer, schema=PANEL_SCHEMA))
        writer.close()
        if summary.loans != loans_in_silver:
            raise PanelTieOutError(
                f"vintage {vintage}: {summary.loans:,} loans in the panel, {loans_in_silver:,} in silver"
            )
        os.replace(tmp, out_path)
    finally:
        writer.close()
        if tmp.exists():
            tmp.unlink()
    return summary


def build_from_duckdb(database: Path, vintage: int, out_root: Path) -> VintageSummary:
    """Build one vintage from the local DuckDB database (read-only)."""
    import duckdb  # local runs only; Databricks will feed build_vintage from Spark instead

    con = duckdb.connect(str(database), read_only=True)
    try:
        loans_in_silver = con.execute(
            "select count(distinct loan_id) from silver.int_freddie__loan_months where vintage_year = ?",
            [vintage],
        ).fetchall()[0][0]
        reader = con.execute(QUERY, [vintage]).to_arrow_reader(BATCH_ROWS)  # streams in batches
        if reader is None:
            raise PanelTieOutError(f"vintage {vintage}: DuckDB returned no result reader")
        return build_vintage(
            reader, vintage, loans_in_silver, Path(out_root) / f"vintage={vintage}" / "panel.parquet"
        )
    finally:
        con.close()
