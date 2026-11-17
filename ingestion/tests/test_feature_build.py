"""The feature build on a tiny made-up book: timing, sampling weights, tie-outs, contract, splits."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cecl import controls
from cecl import features as fx
from ingestion import feature_build as fb
from ingestion import panel_build


def months(start: str, n: int) -> list[str]:
    y, m = int(start[:4]), int(start[4:])
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def panel_row(loan: str, period: str, age: int, status: str, late: int = 0, pause: bool = False) -> dict:
    return {
        "loan_id": loan, "vintage_year": 2015, "period_ym": period, "loan_age": age,
        "current_upb": Decimal("180000.00") - age * 100, "remaining_months": 360 - age, "term_over_360": False,
        "status": status, "exit_reason": None, "reported_months_late": late, "effective_months_late": late,
        "flagged_pause": pause, "status_carried_forward": False, "status_not_yet_due": False,
    }  # fmt: skip


def build_book(root: Path) -> tuple[duckdb.DuckDBPyConnection, list[dict]]:
    rows: list[dict] = []
    # 50 loans paying normally through 2016: 600 no-event months in 2016 (sampled, weighted)
    for i in range(50):
        for k, p in enumerate(months("201601", 12)):
            rows.append(panel_row(f"F15Q1{i:06d}", p, 12 + k, "current"))
    # a loan that falls behind (with a flagged pause) and defaults in January 2017 (validate)
    for k, p in enumerate(months("201609", 4)):
        late = [0, 1, 2, 0][k]
        rows.append(
            panel_row("F15Q2000001", p, 20 + k, "delinquent" if late else "current", late, pause=k == 3)
        )
    rows.append(panel_row("F15Q2000001", "201701", 24, "defaulted", 4))
    # a loan that prepays, and a loan that leaves the data (censored) - both in 2016
    rows += [
        panel_row("F15Q2000002", "201605", 16, "current"),
        panel_row("F15Q2000002", "201606", 17, "prepaid"),
    ]
    rows += [
        panel_row("F15Q2000003", "201610", 21, "current"),
        panel_row("F15Q2000003", "201611", 22, "censored"),
    ]
    folder = root / "local" / "panel" / "vintage=2015"
    folder.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=panel_build.PANEL_SCHEMA), folder / "panel.parquet")

    loans = sorted({r["loan_id"] for r in rows})
    con = duckdb.connect()
    con.execute("create schema silver")
    con.execute(
        "create table silver.stg_freddie__origination (loan_id varchar, vintage_year int, credit_score int, "
        "orig_ltv int, orig_dti int, mi_pct int, orig_upb decimal(18,2), orig_interest_rate decimal(7,3), "
        "term_over_360 boolean, property_state varchar, occupancy_status varchar, loan_purpose varchar, "
        "first_payment_ym varchar, relief_refinance boolean)"
    )
    for j, loan in enumerate(loans):
        state = "GU" if loan == "F15Q2000003" else "TX"  # Guam: no state series (L-023)
        con.execute(
            "insert into silver.stg_freddie__origination values (?, 2015, 720, 80, 36, 0, 200000, 4.5, false, ?, ?, ?, '201502', false)",
            [loan, state, "I" if j == 0 else "P", "C" if j == 1 else "P"],
        )
    con.execute(
        "create table silver.int_freddie__loan_months (loan_id varchar, vintage_year int, period_ym varchar, "
        "current_interest_rate decimal(7,3), eltv int)"
    )
    con.executemany(
        "insert into silver.int_freddie__loan_months values (?, 2015, ?, 4.5, 75)",
        [[r["loan_id"], r["period_ym"]] for r in rows],
    )
    macro = root / "local" / "silver" / "macro"
    macro.mkdir(parents=True)
    all_months = [date(int(p[:4]), int(p[4:]), 1) for p in months("201401", 48)]
    state = [{"state": "TX", "month": m, "variable": v, "value": val}
             for m in all_months for v, val in (("hpi", 100.0 + 0.5 * i) for i, _ in [(all_months.index(m), 0)])]  # fmt: skip
    state += [{"state": "TX", "month": m, "variable": "unemployment_rate", "value": 4.0 + 0.01 * i}
              for i, m in enumerate(all_months)]  # fmt: skip
    pq.write_table(pa.Table.from_pylist(state), macro / "state_monthly.parquet")
    national = [{"month": m, "variable": "mortgage_rate_30y", "value": 4.0} for m in all_months]
    pq.write_table(pa.Table.from_pylist(national), macro / "national_monthly.parquet")
    return con, rows


def test_build_vintage_ties_out_and_writes_by_split(tmp_path: Path) -> None:
    con, rows = build_book(tmp_path)
    result = fb.build_vintage(con, tmp_path, 2015)
    assert all(t.ok for t in result.tie_outs) and len(result.tie_outs) == 4
    assert controls.enforce(result.tie_outs) and result.censored == 1
    assert set(result.rows_by_split) == {"train", "validate"}
    train = pq.read_table(fb.feature_dir(tmp_path, "train") / "vintage=2015.parquet").to_pylist()
    validate = pq.read_table(fb.feature_dir(tmp_path, "validate") / "vintage=2015.parquet").to_pylist()
    assert result.rows_by_split == {"train": len(train), "validate": len(validate)}

    # Sampling: the 2016 stratum has 600+ no-event months, so about 1 in 25 is kept, weighted so
    # the weighted count equals the panel's exactly; events always kept with weight 1.
    none_2016 = [r for r in train if r["outcome"] == fx.NONE and r["period_ym"].startswith("2016")]
    panel_none_2016 = sum(
        1 for r in rows if r["status"] in ("current", "delinquent") and r["period_ym"].startswith("2016")
    )
    assert 0 < len(none_2016) < panel_none_2016 / 5
    assert sum(r["weight"] for r in none_2016) == pytest.approx(panel_none_2016)
    assert [r["weight"] for r in train if r["outcome"] == fx.PREPAY] == [1.0]

    # Timing: January 2017's default sees December's lateness and pause, and the 12-month count.
    (default,) = [r for r in validate if r["outcome"] == fx.DEFAULT]
    assert default["months_late_prev"] == 0.0 and default["forbearance_prev"] == 1.0
    assert default["late_months_12"] == 2.0
    # current LTV uses last month's balance and the state index change since origination (Jan 2015)
    expected = fx.current_ltv(180000 - 23 * 100, 200000, 80, 100.0 + 0.5 * 35, 100.0 + 0.5 * 12)
    assert default["current_ltv"] == pytest.approx(expected)
    assert default["rate_incentive"] == pytest.approx(0.5) and default["calendar_month"] == 1.0


def test_contract_and_eltv_checks(tmp_path: Path) -> None:
    con, _ = build_book(tmp_path)
    fb.build_vintage(con, tmp_path, 2015)
    paths = [p.as_posix() for p in (tmp_path / "local" / "features").rglob("vintage=*.parquet")]
    assert paths
    cur = con.execute(fb.contract_sql(fb.parquet_relation(paths)))
    result = dict(zip([d[0] for d in cur.description], cur.fetchone(), strict=True))
    assert fb.contract_problems(result) == []
    n, mad, corr, within = con.execute(fb.eltv_check_sql(fb.parquet_relation(paths))).fetchone()
    assert n > 0 and mad is not None and 0 <= within <= 1
    broken = dict(result) | {"credit_score__out_of_range": 3, "current_ltv__missing": 0.5}
    problems = fb.contract_problems(broken)
    assert any("credit_score: 3 rows outside" in p for p in problems)
    assert any("current_ltv: 50.0% missing" in p for p in problems)


def test_holdout_goes_to_its_own_folder(tmp_path: Path) -> None:
    assert fb.feature_dir(tmp_path, "test").parent.name == "features_holdout"
    assert fb.feature_dir(tmp_path, "train").parent.name == "features"


def test_horizon_table_keeps_whole_loans_in_validation_and_test(tmp_path: Path) -> None:
    con, rows = build_book(tmp_path)
    result = fb.build_vintage(con, tmp_path, 2015)
    folder = tmp_path / "local" / "features_horizon"
    files = list(folder.rglob("vintage=*.parquet"))
    horizon = [r for f in files for r in pq.read_table(f).to_pylist()]
    assert result.horizon_rows == len(horizon)
    assert {r["split"] for r in horizon} <= {"validate", "test"} and all(r["weight"] == 1.0 for r in horizon)
    # a chosen loan keeps every one of its (non-censored) months in those splits
    for loan in {r["loan_id"] for r in horizon}:
        panel_months = {
            r["period_ym"]
            for r in rows
            if r["loan_id"] == loan and r["period_ym"] >= "201701" and r["status"] != "censored"
        }
        assert {r["period_ym"] for r in horizon if r["loan_id"] == loan} == panel_months


def test_both_dialects_share_the_query() -> None:
    rel = fb.databricks_relations(lambda layer: f"cat.{layer}", 2015)
    spark = fb.training_sql(2015, rel, "spark")
    duck = fb.training_sql(2015, fb.local_relations(Path("data"), 2015), "duckdb")
    assert "conv(substr(md5(" in spark and "add_months(" in spark and "* except (" in spark
    assert "cast('0x' || substr(md5(" in duck and "* exclude (" in duck
    assert (
        "cat.gold.loan_panel where vintage_year = 2015" in spark and "cat.silver.macro_state_monthly" in spark
    )
    # apart from the dialect functions and relation names, the two queries are the same text
    skip = ("md5", "month", "interval", "except", "exclude", "from (", "from read_parquet", "from cat.",
            "from silver", "from data")  # fmt: skip

    def strip(query: str) -> list[str]:
        return [line for line in query.splitlines() if not any(k in line for k in skip)]

    assert strip(spark) == strip(duck)
    assert "'validate', 'test'" in fb.horizon_sql(2015, rel, "spark")


def test_tie_outs_from_counts() -> None:
    checks = fb.tie_outs(2015, (1, 1, 50, 2, 54), 1, 1, 50.0000001)
    assert all(t.ok for t in checks)
    assert "count(*) filter (where outcome = 1)" in fb.totals_sql("t")
    bad = fb.tie_outs(2015, (2, 1, 50, 2, 54), 1, 1, 50.0)
    assert [t.ok for t in bad] == [False, True, True, False]
