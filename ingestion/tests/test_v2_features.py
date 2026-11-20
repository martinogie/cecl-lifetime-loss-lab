"""Model v2 inputs (ADR-009 Amendment 3) on the tiny made-up book, and the household-stress loader."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cecl import features as fx
from ingestion import feature_build as fb
from ingestion import household
from ingestion import macro_contracts as mc
from ingestion.tests.test_feature_build import build_book, months


def _household(root: Path) -> None:
    rows = [{"month": date(int(p[:4]), int(p[4:]), 1), "variable": v, "value": val, "source": "test"}
            for i, p in enumerate(months("201401", 48))
            for v, val in (("cpi_inflation_12", 2.0 + 0.1 * i), ("real_income_growth_12", 1.0),
                           ("card_delinquency", 3.0))]  # fmt: skip
    pq.write_table(
        pa.Table.from_pylist(rows), root / "local" / "silver" / "macro" / "household_monthly.parquet"
    )


def test_v2_columns_follow_the_loans_own_history(tmp_path: Path) -> None:
    pytest.importorskip("pandas")  # DuckDB .df(); runs in the ml job
    con, _ = build_book(tmp_path)
    _household(tmp_path)
    rel = fb.local_relations(tmp_path, 2015)
    table = con.sql(fb.transitions_sql(2015, rel, "duckdb", v2=True)).df().set_index(["loan_id", "period_ym"])
    # The delinquent loan: ages 20-23 in Sep-Dec 2016 at 0, 1, 2, 0 months late (pause in Dec), default Jan 2017
    jan, dec = table.loc[("F15Q2000001", "201701")], table.loc[("F15Q2000001", "201612")]
    assert jan["months_since_first_miss"] == 3  # first late at age 21; age 24 now
    assert jan["worst_state_6"] == 2 and jan["paused_12"] == 1 and jan["state_last_month"] == 2
    assert dec["months_since_first_miss"] == 2 and dec["worst_state_6"] == 1 and dec["paused_12"] == 0
    assert jan["split"] == fx.V2_TRAIN  # 2017 is a v2 training month (v1: validation)
    # household inputs from the month before: December 2016 is the 36th month from January 2014
    assert jan["cpi_inflation_12"] == pytest.approx(2.0 + 0.1 * 35)
    clean = table.loc[("F15Q2000002", "201606")]  # a clean loan that prepays (kept, not sampled)
    assert clean["months_since_first_miss"] == 0 and clean["worst_state_6"] == 0
    # v1's query is untouched by the flag
    v1 = con.sql(fb.transitions_sql(2015, rel, "duckdb")).df()
    assert "worst_state_6" not in v1.columns and set(v1["split"]) <= {fx.TRAIN, fx.VALIDATE}
    assert set(fx.V2_STORED) <= set(table.columns)


def test_v2_split_and_derived_rate_lock_inputs() -> None:
    assert fx.v2_split_of("201912") == "train" and fx.v2_split_of("202006") == "covid"
    assert fx.v2_split_of("202207") == "train" and fx.v2_split_of("202406") == "train"
    assert fx.v2_split_of("202407") == "holdout" and fx.v2_split_of("202606") == "holdout"
    with pytest.raises(ValueError):
        fx.v2_split_of("202607")
    X = {"rate_incentive": np.array([0.5, -1.0, -2.5]), "loan_age": np.array([12.0, 24.0, 120.0]),
         "months_since_first_miss": np.array([0.0, 3.0, 0.0])}  # fmt: skip
    out = fx.with_derived(X, fx.v2_features())
    assert list(out["locked_1"]) == [0, 1, 1] and list(out["locked_2"]) == [0, 0, 1]
    assert list(out["locked_x_age"]) == [0, 2, 10] and list(out["ever_missed"]) == [0, 1, 0]
    assert len(fx.v2_features()) == 28 and fx.v2_features(("cpi_inflation_12",))[-1] == "cpi_inflation_12"
    with pytest.raises(ValueError):
        fx.v2_features(("made_up",))


def test_household_loader_checks_and_transforms(tmp_path: Path) -> None:
    def csv(sid: str, rows: list[tuple[str, str]]) -> bytes:
        return ("observation_date," + sid + "\n" + "\n".join(f"{d},{v}" for d, v in rows)).encode()

    monthly = [f"{1990 + k // 12}-{k % 12 + 1:02d}-01" for k in range(36 * 12 + 10)]  # to Oct 2025
    bodies = {
        "CPIAUCSL": csv("CPIAUCSL", [(d, "." if d == "2025-10-01" else f"{130 * 1.002 ** i:.3f}") for i, d in enumerate(monthly)]),
        "DSPIC96": csv("DSPIC96", [(d, f"{9000 + i:.1f}") for i, d in enumerate(monthly)]),
        "DRCCLACBS": csv("DRCCLACBS", [(f"{1991 + k // 4}-{3 * (k % 4) + 1:02d}-01", "4.0") for k in range(139)]),
    }  # fmt: skip

    def get(url: str) -> bytes:
        return bodies[url.split("id=")[1].split("&")[0]]

    out = household.load(tmp_path, date(2025, 11, 20), get=get)
    assert all(f["ok"] for f in out["findings"])
    table = pq.read_table(tmp_path / "local" / "silver" / "macro" / "household_monthly.parquet").to_pylist()
    cpi = {r["month"]: r["value"] for r in table if r["variable"] == "cpi_inflation_12"}
    assert cpi[date(2000, 1, 1)] == pytest.approx(100 * (1.002**12 - 1), rel=1e-3)
    assert date(2025, 10, 1) in cpi  # the known gap is filled, so the series has no hole
    card = {r["month"]: r["value"] for r in table if r["variable"] == "card_delinquency"}
    assert date(1991, 2, 1) not in card and card[date(1991, 3, 1)] == 4.0  # Q1 known once it has ended
    # an unexpected gap stops the load
    bodies["DSPIC96"] = csv("DSPIC96", [(d, "." if d == "2000-05-01" else "9000") for d in monthly])
    with pytest.raises(mc.ContractViolation):
        household.load(tmp_path, date(2025, 11, 20), get=get)


def test_quarter_carried_only_after_it_ends() -> None:
    q = [(date(2020, 1, 1), 2.0), (date(2020, 4, 1), 3.0)]
    out = dict(household.quarter_ended_by(q, date(2020, 8, 1)))
    assert out[date(2020, 3, 1)] == 2.0 and out[date(2020, 5, 1)] == 2.0 and out[date(2020, 6, 1)] == 3.0


def test_v2_projection_tables(tmp_path: Path) -> None:
    pytest.importorskip("pandas")  # DuckDB .df(); runs in the ml job
    con, rows = build_book(tmp_path)
    _household(tmp_path)
    rel = fb.local_relations(tmp_path, 2015)
    start = con.sql(fb.v2_projection_starts_sql(2015, rel, "duckdb", ("201701",))).df()
    alive = {r["loan_id"] for r in rows if r["period_ym"] == "201701" and r["status"] != "censored"}
    assert set(start["loan_id"]) == alive and set(start["start_ym"]) == {"201701"}
    assert {"worst_state_6", "upb_start", "hpi_orig"} <= set(start.columns)
    hist = con.sql(fb.v2_projection_history_sql(rel["panel"], ("201701",))).df().set_index("loan_id")
    h = hist.loc["F15Q2000001"]
    assert [h[f"state_hist_{j}"] for j in range(1, 6)] == [0, 2, 1, 0, 0]
    assert h["pause_hist_1"] == 1 and h["pause_hist_2"] == 0
    acts = con.sql(fb.v2_projection_actuals_sql(rel["panel"], (("201701", "201912"),))).df()
    assert acts.set_index("loan_id").loc["F15Q2000001", "last_status"] == "defaulted"
