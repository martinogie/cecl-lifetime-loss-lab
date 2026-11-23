"""LGD and EAD on small made-up data (needs requirements-ml.txt; skipped without it)."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
duckdb = pytest.importorskip("duckdb")

from cecl import ead, lgd  # noqa: E402


# ---------------------------------------------------------------------------- EAD
def test_scheduled_balance() -> None:
    # 100,000 at 6% over 360 months (payment 599.55): balance after 12 payments 98,771.99
    b = ead.scheduled_balance(np.array([100_000.0]), np.array([6.0]), np.array([360]), 12)
    assert b[0] == pytest.approx(98_771.99, abs=0.01)
    assert ead.scheduled_balance(np.array([1200.0]), np.array([0.0]), np.array([12]), 6)[0] == pytest.approx(
        600.0
    )
    assert (
        ead.scheduled_balance(np.array([5000.0]), np.array([5.0]), np.array([10]), 10)[0] == 0.0
    )  # term over


def test_partial_prepayment_lowers_the_balance_by_age_band() -> None:
    assert list(ead.age_band([0, 11, 12, 59, 60, 400])) == [0, 0, 1, 2, 3, 4]
    args = (np.array([100_000.0]), np.array([6.0]), np.array([360]), np.array([10.0]))
    none = ead.project(*args, 12, {})
    some = ead.project(*args, 12, {0: 0.001, 1: 0.002})
    # 2 months in band 0 (ages 10, 11) and 10 in band 1 (12-21)
    assert some[0] == pytest.approx(none[0] * 0.999**2 * 0.998**10)


def _perf() -> pd.DataFrame:
    """Two current, unmodified loans paying exactly on schedule, one paying 100 extra a month."""
    rows = []
    for loan, extra in (("A", 0.0), ("B", 100.0)):
        upb, rate, rem = 100_000.0, 6.0, 360
        for k in range(40):
            ym = f"{2016 + (k // 12)}{k % 12 + 1:02d}"
            rows.append({"loan_id": loan, "period": ym, "loan_age": 20 + k, "current_upb": round(upb, 2),
                         "current_interest_rate": rate, "remaining_months": rem, "delinquency_status": "00",
                         "modification_flag": None, "zero_balance_code": None})  # fmt: skip
            upb = (
                float(ead.scheduled_balance(np.array([upb]), np.array([rate]), np.array([rem]), 1)[0]) - extra
            )
            rem -= 1
    return pd.DataFrame(rows)


def test_curtailment_and_check_sql_on_made_up_loans() -> None:
    perf = _perf()  # noqa: F841 - read by DuckDB by name
    con = duckdb.connect()
    curt = con.sql(ead.curtailment_sql("perf", "201601", "201612")).df()
    assert len(curt) == 1 and int(curt.band[0]) == 1  # ages 20-31: band 12-36 months
    assert curt.rate[0] == pytest.approx(100.0 / 2 / 99_000, rel=0.05)  # one of two loans pays ~0.1% extra
    chk = con.sql(ead.check_sql("perf", "201701", (12,))).df()
    rows = ead.check(chk, {1: 0.0}, (12,))
    assert rows[0]["loans"] == 2 and rows[0]["schedule_only"] > 1.0  # B paid down faster than its schedule


# ---------------------------------------------------------------------------- LGD
def _events(n: int = 3000, seed: int = 3) -> pd.DataFrame:
    """Loss events where severity rises with LTV at default and falls with MI cover."""
    rng = np.random.default_rng(seed)
    f = pd.DataFrame({"ltv_default": rng.uniform(40, 140, n), "hpi_change_12": rng.normal(0.02, 0.08, n),
                      "mi_pct": rng.choice([0, 0, 25, 30], n).astype(float), "judicial": rng.integers(0, 2, n).astype(float),
                      "log_orig_upb": np.log(rng.uniform(80_000, 400_000, n)), "credit_score": rng.uniform(600, 800, n),
                      "investor": rng.integers(0, 2, n).astype(float), "default_upb": rng.uniform(50_000, 300_000, n)})  # fmt: skip
    sev = 0.006 * (f.ltv_default - 70) - 0.004 * f.mi_pct + 0.05 * f.judicial + rng.normal(0, 0.08, n)
    f["severity"] = sev
    return f


def test_two_step_lgd_follows_ltv_and_mi_and_rises_in_a_downturn() -> None:
    f = _events()
    model = lgd.TwoStepLGD(max_iter=80).fit(f)
    base = f.iloc[:1].copy()
    lo, hi = base.assign(ltv_default=60.0), base.assign(ltv_default=130.0)
    assert model.predict(lgd.matrix(hi))[0] > model.predict(lgd.matrix(lo))[0]
    assert (
        model.predict(lgd.matrix(base.assign(mi_pct=30.0)))[0]
        <= model.predict(lgd.matrix(base.assign(mi_pct=0.0)))[0]
    )
    down = lgd.downturn(model, f)
    assert [r["hpi_shock"] for r in down] == [0.0, -0.1, -0.2, -0.3]
    assert down[-1]["lgd"] > down[0]["lgd"]  # prices 30% lower -> higher LGD
    ev = lgd.evaluate(model, f, {"judicial": f.judicial.to_numpy()})
    assert 0.8 < ev["ratio"] < 1.25 and len(ev["groups"]["judicial"]) == 2


def test_event_sql_follows_every_default_to_its_outcome() -> None:
    con = duckdb.connect()
    # L1 defaults then is sold at a loss; L2 defaults then pays off; L3 defaults then is current again;
    # L4 defaults and is still 4 months late (unresolved); L5 never defaults
    con.execute("""create table perf as select * from (values
        ('L1', '201112', '04', null, null, null, null), ('L1', '201206', '06', '09', '201206', 180000.0, 54000.0),
        ('L2', '201112', '03', null, null, null, null), ('L2', '201203', '00', '01', '201203', 150000.0, null),
        ('L3', '201112', '03', null, null, null, null), ('L3', '201606', '00', null, null, null, null),
        ('L4', '201112', '04', null, null, null, null), ('L5', '201112', '00', null, null, null, null)
        ) t(loan_id, period, delinquency_status, zero_balance_code, zero_balance_date, zero_balance_removal_upb, actual_loss)""")
    con.execute("""create table orig as select loan_id, 'FL' as property_state, 200000.0 as orig_upb, 80.0 as orig_ltv,
        0.0 as mi_pct, 700.0 as credit_score, 'P' as occupancy_status, '200601' as first_payment_date
        from (values ('L1'), ('L2'), ('L3'), ('L4'), ('L5')) v(loan_id)""")
    con.execute("""create table panel as select * from (values ('L1', '201112', 'defaulted', 180000.0),
        ('L2', '201112', 'defaulted', 150000.0), ('L3', '201112', 'defaulted', 170000.0),
        ('L4', '201112', 'defaulted', 160000.0), ('L5', '201112', 'current', 190000.0)) t(loan_id, period_ym, status, current_upb)""")
    con.execute("""create table hpi as select * from (values ('FL', '200512', 200.0), ('FL', '201012', 160.0),
        ('FL', '201112', 150.0)) t(state, ym, hpi)""")
    out = con.sql(lgd.event_sql("perf", "orig", "panel", "hpi")).df().set_index("loan_id")
    assert sorted(out.index) == ["L1", "L2", "L3", "L4"]  # every default, not only loss events
    assert out.loc["L1", "outcome"] == "loss event" and out.loc["L1", "severity"] == pytest.approx(0.30)
    assert out.loc["L2", "outcome"] == "paid off" and out.loc["L2", "severity"] == 0.0
    assert out.loc["L3", "outcome"] == "current again" and out.loc["L3", "severity"] == 0.0
    assert out.loc["L4", "outcome"].startswith("unresolved") and pd.isna(out.loc["L4", "severity"])
    # value 250,000 x 150/200 = 187,500 at default; 180,000 / 187,500 = 96%
    assert out.loc["L1", "ltv_default"] == pytest.approx(96.0)
    assert out.loc["L1", "hpi_change_12"] == pytest.approx(150 / 160 - 1) and out.loc["L1", "judicial"] == 1.0


def test_portable_lgd_predicts_the_same_without_fit_time_generators() -> None:
    f = _events()
    model = lgd.TwoStepLGD(max_iter=40).fit(f)
    p = lgd.portable(model)
    assert np.array_equal(p.predict(lgd.matrix(f)), model.predict(lgd.matrix(f)))
    for part in (p.step1, p.step2):
        assert getattr(part, "_feature_subsample_rng", None) is None
