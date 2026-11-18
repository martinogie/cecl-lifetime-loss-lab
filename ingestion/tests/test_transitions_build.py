"""The multi-state transition table (ADR-011) on the tiny made-up book: states, sampling, tie-outs."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from cecl import controls
from cecl import features as fx
from ingestion import feature_build as fb
from ingestion.tests.test_feature_build import build_book


def read(folder: Path) -> list[dict]:
    return [r for f in sorted(folder.rglob("vintage=*.parquet")) for r in pq.read_table(f).to_pylist()]


def test_state_rule() -> None:
    assert fx.state_of("current", 0) == fx.CURRENT
    assert fx.state_of("delinquent", 1) == fx.LATE_30 and fx.state_of("delinquent", 2) == fx.LATE_60
    assert fx.state_of("defaulted", 4) == fx.DEFAULTED and fx.state_of("prepaid", 2) == fx.PREPAID
    with pytest.raises(ValueError):
        fx.state_of("censored", 0)
    with pytest.raises(ValueError):
        fx.state_of("delinquent", 3)  # 3+ months late is a default (ADR-002 B1), never a live row
    assert "months_late_prev" not in fx.TRANSITION_FEATURES and len(fx.TRANSITION_FEATURES) == 21


def test_transition_table_ties_out_and_keeps_every_move(tmp_path: Path) -> None:
    con, rows = build_book(tmp_path)
    result = fb.build_transitions_vintage(con, tmp_path, 2015)
    assert controls.enforce(result.tie_outs) and all(t.ok for t in result.tie_outs)
    table = read(tmp_path / "local" / "transitions")
    assert sum(result.rows_by_split.values()) == len(table)
    assert {"state_prev", "state", "weight"} <= set(table[0]) and "outcome" not in table[0]
    assert "months_late_prev" not in table[0] and "upb_start" not in table[0]

    # The delinquent loan: 0 -> 1 -> 2 -> 0 (a cure from 60) -> default; every move kept, weight 1.
    path = [
        (r["period_ym"], r["state_prev"], r["state"], r["weight"])
        for r in table
        if r["loan_id"] == "F15Q2000001"
    ]
    moves = [(sp, s) for _, sp, s, _ in path]
    assert (0, 1) in moves and (1, 2) in moves and (2, 0) in moves and (0, fx.DEFAULTED) in moves
    assert all(w == 1.0 for _, sp, s, w in path if (sp, s) != (0, 0))
    # current -> current months in 2016 are sampled and weighted back to the panel's count exactly
    stay_2016 = [
        r for r in table if (r["state_prev"], r["state"]) == (0, 0) and r["period_ym"].startswith("2016")
    ]
    panel_stay = result.tie_outs[0].expected  # moves current -> current, panel side (all years)
    assert sum(r["weight"] for r in table if (r["state_prev"], r["state"]) == (0, 0)) == pytest.approx(
        panel_stay
    )
    assert 0 < len(stay_2016) < 600 / 5


def test_transition_tie_outs_catch_a_missing_move() -> None:
    panel = (1, 1, 10, 0, 12)  # defaults, prepays, live, censored, rows
    moves = {(0, 0): 8, (0, 1): 1, (1, 0): 1, (0, 3): 1, (0, 4): 1}
    ok = fb.transition_tie_outs(2015, panel, moves, {k: float(v) for k, v in moves.items()})
    assert all(t.ok for t in ok)
    short = {k: float(v) for k, v in moves.items() if k != (1, 0)}
    failed = [t.name for t in fb.transition_tie_outs(2015, panel, moves, short) if not t.ok]
    assert any("30 -> current" in n for n in failed) and any("starting 30" in n for n in failed)


def test_projection_tables_hold_loans_alive_at_the_start(tmp_path: Path) -> None:
    con, rows = build_book(tmp_path)
    result = fb.build_transitions_vintage(con, tmp_path, 2015)
    folder = tmp_path / "local" / "projection" / "split=validate"
    start, actuals = read(folder / "start"), read(folder / "actuals")
    alive = {r["loan_id"] for r in rows if r["period_ym"] == "201701" and r["status"] != "censored"}
    assert {r["loan_id"] for r in start} == alive and result.projection_loans == len(alive)
    (s,) = start
    assert s["state_prev"] == 0 and s["state"] == fx.DEFAULTED  # starting current, defaults that month
    assert s["upb_start"] == pytest.approx(180000 - 23 * 100) and s["hpi_orig"] == pytest.approx(
        100.0 + 0.5 * 12
    )
    assert actuals == [{"loan_id": "F15Q2000001", "last_ym": "201701", "last_status": "defaulted"}]


def test_state_contract_and_transition_contract(tmp_path: Path) -> None:
    con, _ = build_book(tmp_path)
    fb.build_transitions_vintage(con, tmp_path, 2015)
    rel = fb.parquet_relation([p.as_posix() for p in (tmp_path / "local" / "transitions").rglob("*.parquet")])
    assert con.execute(fb.state_check_sql(rel)).fetchone() == (0, 0)
    cur = con.execute(fb.contract_sql(rel, fx.TRANSITION_FEATURES))
    result = dict(zip([d[0] for d in cur.description], cur.fetchone(), strict=True))
    assert fb.contract_problems(result, fx.TRANSITION_FEATURES) == []


def test_both_dialects_share_the_transition_query() -> None:
    rel = fb.databricks_relations(lambda layer: f"cat.{layer}", 2015)
    spark = fb.transitions_sql(2015, rel, "spark")
    duck = fb.transitions_sql(2015, fb.local_relations(Path("data"), 2015), "duckdb")
    skip = ("md5", "month", "interval", "except", "exclude", "from (", "from read_parquet", "from cat.",
            "from silver", "from data")  # fmt: skip

    def strip(query: str) -> list[str]:
        return [line for line in query.splitlines() if not any(k in line for k in skip)]

    assert strip(spark) == strip(duck)
    assert "max_by(status, period_ym)" in fb.projection_actuals_sql("p")
    hist = fb.projection_history_sql("p")
    assert "rn <= 12" in hist and "period_ym < '201701'" in hist and "late_hist_12" in hist
    assert "where r.period_ym = '201701'" in fb.projection_start_sql(2015, rel, "spark")
