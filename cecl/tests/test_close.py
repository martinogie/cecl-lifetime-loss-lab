"""The quarterly close (cecl/close.py) on small made-up books (needs requirements-ml.txt; skipped without it)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
duckdb = pytest.importorskip("duckdb")

from cecl import close  # noqa: E402

SC = ("baseline", "adverse", "severely_adverse")


# ---------------------------------------------------------------- quarters
def test_quarter_helpers() -> None:
    assert close.quarter_end_ym(date(2026, 8, 14)) == "202609"
    assert close.quarter_end_ym(date(2027, 1, 2)) == "202703"
    assert close.prior_quarter("202609") == "202606"
    assert close.prior_quarter("202703") == "202612"
    assert close.next_quarter("202612") == "202703" and close.next_quarter("202606") == "202609"
    assert close.quarter_months("202609") == ("202607", "202609")
    assert close.quarter_tag("202612") == "q4_2026"
    assert close.is_quarter_end("202606") and not close.is_quarter_end("202605")
    for bad in ("202605", "2026Q2", ""):
        with pytest.raises(ValueError):
            close.prior_quarter(bad)


def test_weights_and_severe_sensitivity() -> None:
    assert sum(close.WEIGHTS.values()) == pytest.approx(1.0)
    w = close.severe_weights(0.25)
    assert w["severely_adverse"] == 0.25 and w["baseline"] == pytest.approx(2 * w["adverse"])
    assert close.weighted({"baseline": 100.0, "adverse": 200.0, "severely_adverse": 1000.0}) == pytest.approx(
        220.0
    )


def test_check_settings() -> None:
    close.check_settings(pd.DataFrame({"x": [1]}), "old book")  # no columns: ADR-003's 24 / 12
    close.check_settings(pd.DataFrame({"forecast_months": [24], "reversion_months": [12]}), "book")
    with pytest.raises(ValueError, match="forecast/reversion"):
        close.check_settings(pd.DataFrame({"forecast_months": [12], "reversion_months": [12]}), "book")


# ---------------------------------------------------------------- made-up books
VINTAGE = {"a": 2018, "b": 2020, "c": 2026}  # origination year of each made-up loan


def priced(ids: list[str], totals: list[float]) -> object:
    n = len(ids)
    return pd.DataFrame({"loan_id": ids, "vintage_year": [VINTAGE[i] for i in ids], "upb_start": [100_000.0] * n,
                         "total": totals, "forecast": totals, "reversion": [0.0] * n, "tail": [0.0] * n,
                         "lifetime_pd": [0.01] * n})  # fmt: skip


def frames(ids: list[str], base: list[float], scale: float = 1.0) -> dict[str, object]:
    return {s: priced(ids, [t * scale * k for t in base]) for s, k in zip(SC, (1.0, 3.0, 9.0), strict=True)}


def no_pipe() -> object:
    return pd.DataFrame({"loan_id": [], "balance": [], "reserve": []})


REGISTER = {"booked_total": 0.0, "overlays": []}


def test_date_block_and_tie_out() -> None:
    d = close.date_block(frames(["a", "b"], [100.0, 50.0]), no_pipe(), "202606", REGISTER, [])
    assert all(t["passes"] for t in d["tie_outs"].values())
    # weighted: 0.6 x 150 + 0.3 x 450 + 0.1 x 1350 = 360
    assert d["weighted_reserve"] == pytest.approx(360.0)
    assert d["total_allowance"] == pytest.approx(360.0)
    assert d["fed_scenario_year"] == 2026
    assert len(d["weight_sensitivity"]) == len(close.SEVERE_RANGE)


def test_rollforward_reconciles_to_the_cent() -> None:
    orig = pd.DataFrame({"loan_id": ["a", "b", "c"], "vintage_year": [2018, 2020, 2026]})
    start = close.booked_frame(frames(["a", "b"], [100.004, 50.0]), no_pipe(), orig, 0.0)
    end = close.booked_frame(frames(["b", "c"], [60.0, 10.0]), no_pipe(), orig, 0.0)
    wo = pd.DataFrame(
        {"loan_id": ["a"], "gross_write_off": [30.0], "recoveries": [5.0], "group": ["2018 and older"]}
    )
    rf = close.rollforward(start, end, wo)
    assert rf["reconciles_to_the_cent"]
    t = {k: Decimal(v) for k, v in rf["total"].items()}
    assert t["start"] + t["provision"] + t["write_offs"] + t["recoveries"] == t["end"]
    assert t["write_offs"] == Decimal("-30.00")
    assert {r["group"] for r in rf["by_group"]} == {"2018 and older", "2019-2021", "2025-2026"}


def test_bridge_steps_reconcile_and_tie() -> None:
    P, R = "202606", "202609"
    old = frames(["a", "b"], [100.0, 50.0])
    new = frames(["b", "c"], [70.0, 20.0])
    new_old_economy = frames(["b", "c"], [60.0, 20.0])
    runs = {}
    for s in SC:
        runs[(P, P, s)], runs[(R, R, s)], runs[(R, P, s)] = old[s], new[s], new_old_economy[s]
    da = close.date_block(old, no_pipe(), P, REGISTER, [])
    pipe = pd.DataFrame({"loan_id": ["z"], "balance": [1000.0], "reserve": [400.0]})
    db = close.date_block(new, pipe, R, {"booked_total": 25.0, "overlays": []}, [])
    br = close.finish_bridge(close.bridge(runs, P, R), da, db)
    assert br["reconciles"] and br["ties_to_total_allowance"]
    st = br["steps"]
    assert list(st)[-1] == "end"
    assert st["exits"] == pytest.approx(-0.6 * 100 - 0.3 * 300 - 0.1 * 900)
    assert st["overlays"] == 25.0 and st["defaulted_loan_reserve_change"] == 400.0
    assert st["economic_forecast"] == pytest.approx(0.6 * 10 + 0.3 * 30 + 0.1 * 90)


def test_booked_frame_keeps_overlays_and_pipeline() -> None:
    orig = pd.DataFrame({"loan_id": ["a", "z"], "vintage_year": [2018, 2007]})
    pipe = pd.DataFrame({"loan_id": ["z"], "balance": [1000.0], "reserve": [400.0]})
    f = close.booked_frame(frames(["a"], [10.0]), pipe, orig, 12.5)
    assert list(f.columns) == close.BOOKED_COLUMNS
    assert f["reserve"].sum() == pytest.approx(0.6 * 10 + 0.3 * 30 + 0.1 * 90 + 400 + 12.5)
    assert set(f["group"]) == {"2018 and older", "Overlays (ADR-006)"}
    assert f.loc[f["loan_id"] == "z", "group"].item() == "2018 and older"  # vintage from the origination file


# ---------------------------------------------------------------- loan data through DuckDB
class FlatLGD:
    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), 0.25)


def relations(con: object) -> dict[str, str]:
    perf = pd.DataFrame({
        "loan_id": ["p1", "p2", "w1", "w2", "w3"],
        "period": ["202609", "202609", "202608", "202608", "202605"],
        "current_upb": [80_000.0, 50_000.0, 0.0, 0.0, 0.0],
        "delinquency_status": ["RA", "01", "", "", ""],
        "zero_balance_code": [None, None, "09", "03", "09"],
        "zero_balance_date": [None, None, "202608", "202608", "202605"],
        "zero_balance_removal_upb": [None, None, 90_000.0, 40_000.0, 10_000.0],
        "actual_loss": [None, None, 20_000.0, None, 1_000.0],
        "mi_recoveries": [None, None, -5_000.0, None, None],
        "non_mi_recoveries": [None, None, None, None, None],
    })  # fmt: skip
    ev_cols = {c: [1.0, 1.0, 1.0] for c in ("ltv_default", "hpi_change_12", "mi_pct", "judicial", "log_orig_upb",
                                             "credit_score", "investor")}  # fmt: skip
    events = pd.DataFrame({"loan_id": ["p1", "w2", "late"], "default_ym": ["202601", "202605", "202612"],
                           "default_upb": [85_000.0, 41_000.0, 1.0], **ev_cols})  # fmt: skip
    con.register("perf_t", perf)  # type: ignore[attr-defined]
    con.register("events_t", events)  # type: ignore[attr-defined]
    return {"perf": "perf_t", "events": "events_t", "orig": "unused"}


def test_defaulted_pipeline_and_losses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(close.lgd, "matrix", lambda frame: np.zeros((len(frame), 7)))
    con = duckdb.connect()
    rel = relations(con)
    pipe = close.defaulted_pipeline(con, rel, "202609", FlatLGD())
    assert list(pipe["loan_id"]) == ["p1"]  # 90+ / REO, no zero balance, defaulted by R
    assert pipe["reserve"].sum() == pytest.approx(80_000 * 0.25)
    wo = close.losses(con, rel, "202607", "202609", FlatLGD())
    by = wo.set_index("loan_id")
    assert by.loc["w1", "net_write_off"] == 20_000 and by.loc["w1", "recoveries"] == 5_000
    assert by.loc["w1", "gross_write_off"] == 25_000
    assert bool(by.loc["w2", "estimated"]) and by.loc["w2", "net_write_off"] == pytest.approx(40_000 * 0.25)
    history = close.losses(con, rel, "199901", "202609")  # no model: estimated dispositions left out
    assert set(history["loan_id"]) == {"w1", "w3"}


def test_reserve_adequacy() -> None:
    hist = [{"reserve_ym": "202603", "total_allowance": 200.0}, {"reserve_ym": "202606", "total_allowance": 300.0},
            {"reserve_ym": "202609", "total_allowance": 310.0}]  # fmt: skip
    out = close.reserve_adequacy(hist, {"202603": 20.0, "202606": 3.0}, "202609")
    assert [o["reserve_ym"] for o in out] == ["202603", "202606"]
    assert out[0]["months_elapsed"] == 6 and out[0]["share_of_reserve_used"] == pytest.approx(0.1)
    assert out[1]["months_elapsed"] == 3


# ---------------------------------------------------------------- pricing, benchmarks, disclosures
def projection_book(n: int = 4) -> object:
    cols = {f"pd_{k}": [0.001] * n for k in range(1, close.engine.MONTHLY + 1)}
    cols |= {f"pp_{k}": [0.01] * n for k in range(1, close.engine.MONTHLY + 1)}
    return pd.DataFrame({"loan_id": [f"l{i}" for i in range(n)], "vintage_year": [2005, 2010, 2020, 2026][:n],
                         "property_state": ["TX", "TX", "CA", "CA"][:n], "credit_score": [610, 650, 700, 760][:n],
                         "upb_start": [100_000.0] * n, "current_ltv": [55.0, 75.0, 95.0, 120.0][:n], "first_state": [0] * n,
                         "remaining_start": [240] * n, **cols})  # fmt: skip


def test_price_passes_the_scenario_to_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class Sc:
        long_run = {"hpi_growth": 0.03}
        macro = "macro"

    def build(name: str, E: str, *a: object, **kw: object) -> Sc:
        seen.update(name=name, E=E, weights=a[4], horizon=kw["horizon"])
        return Sc()

    def price(book: object, p_def: np.ndarray, p_pre: np.ndarray, macro: object, R: str, *a: object) -> dict:
        seen.update(R=R, shape=p_def.shape)
        return {"total": np.full(len(p_def), 5.0)}

    monkeypatch.setattr(close.scenarios, "build", build)
    monkeypatch.setattr(close.engine, "price", price)
    book = projection_book()
    inp = {"lgd": None, "curtail": {}, "state": None, "national": None, "fed": {2026: None}, "long_run": None}
    f = close.price(book, "202609", "202606", "adverse", inp, close.state_weights(book))
    assert seen["name"] == "adverse" and seen["E"] == "202606" and seen["R"] == "202609"
    assert seen["weights"] == {"CA": 200_000.0, "TX": 200_000.0}
    assert seen["horizon"] == close.engine.MONTHLY + 3 + 3 and seen["shape"] == (4, close.engine.MONTHLY)
    assert list(f["total"]) == [5.0] * 4 and "first_state" in f.columns


def test_disclosures_tables() -> None:
    book = projection_book()
    orig = pd.DataFrame({"loan_id": ["w1", "w2"], "vintage_year": [2005, 2020]})
    wo = pd.DataFrame({"loan_id": ["w1", "w2"], "gross_write_off": [10.0, 5.0]})
    d = close.disclosures(book, wo, wo.iloc[:1], orig, "202609")
    assert d["credit_quality_fico_by_origination_year"]["<620"]["2005"] == 100_000.0
    assert d["credit_quality_current_ltv_by_origination_year"][">100"]["2026"] == 100_000.0
    assert d["gross_write_offs_by_origination_year_q3_2026"] == {"2005": 10.0}
    assert d["gross_write_offs_by_origination_year_ytd_2026"] == {"2005": 10.0, "2020": 5.0}


def test_benchmarks() -> None:
    book = projection_book()
    lev = pd.DataFrame(
        {"loan_id": ["a", "b"], "disp_ym": ["200901", "201006"], "net_write_off": [1000.0, 500.0]}
    )
    bal = pd.Series({"2009": 1e6, "2010": 1e6, "2011": 1e6})
    orig = pd.DataFrame({"loan_id": ["a", "b"], "vintage_year": [2005, 2005]})
    ob = pd.Series({2005: 1e6, 2010: 1e6, 2020: 1e6, 2026: 1e6})
    out = close.benchmarks(lev, bal, orig, ob, book, "202609")
    lr = out["loss_rate_method"]
    assert lr["annual_net_loss_rate_1999_2025"] == pytest.approx((1000 / 1e6 + 500 / 1e6) / 3)
    assert lr["reserve"] > 0 and lr["expected_remaining_life_years"] > 3
    v = out["vintage_method"]
    assert v["mature_vintages"] == [2005, 2010] and v["by_vintage"]["2026"]["age"] == 0
    assert v["average_cumulative_loss_curve"][-1] == pytest.approx(0.00075)
