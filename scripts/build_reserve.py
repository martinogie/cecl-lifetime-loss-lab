"""Phase 4: price the reserve, weight the scenarios, and build every reserve output (aggregates only).

    CECL_ENV=local python -m scripts.build_reserve [--write]

Inputs (git-ignored, loan-level): data/local/reserve/book_<R>_<E>_<scenario>.parquet from the Databricks
job `reserve_projection` (v2 default and payoff chances, months 1-36 after the reserve month R, under the
economy dated E), the scenario paths, the Phase 3 LGD model and EAD rates, the Freddie loss fields and the
panel. Outputs (aggregates only): docs/phase4/reserve_2026Q2.json; the allowance memo is written from it.

What it builds:
1. Each loan's lifetime expected loss per scenario (`cecl.engine.price`); tie-out: book = sum of loans.
2. The weighted reserve (ADR-005: losses weighted 60/30/10) at 31 December 2025, 31 March and 30 June 2026,
   and a weight-sensitivity table (severe weight 5% to 25%). Each date uses the Fed scenarios published by
   then (`scenarios.fed_year`): 2025 for December 2025, 2026 for March and June 2026.
3. The overlay decision (L-038, Option B: none on the baseline) and its required sensitivities: every
   reserve with the +34% default uplift alongside, and the severely adverse reserve with and without it.
4. Benchmarks: the loss-rate method and the vintage method (M-05, M-06).
5. The Q1 and Q2 2026 rollforwards (start + provision - write-offs + recoveries = end, to the cent), overall
   and by loan group, and the reserve bridges (exits, new loans, credit quality and ageing, economic
   forecast, model changes, overlays, defaulted-loan reserve change).
7. The overlay register for each date (ADR-006, `cecl.overlays`, log `docs/governance/overlay_log.json`):
   only approved overlays that pass every rule are booked, on top of the modelled reserve.
6. Disclosure tables: rollforward by loan group; credit quality (FICO and LTV bands) by origination year;
   gross write-offs by origination year.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import numpy as np

from cecl import engine, environment, lgd, overlays, scenarios
from cecl.models import multistate

LOCAL = Path("data/local")
RES = LOCAL / "reserve"
OUT = Path("docs/phase4/reserve_2026Q2.json")
Q0, Q1, Q2 = "202512", "202603", "202606"
QUARTERS = ((Q0, Q1, "q1_2026", "202601", "202603"), (Q1, Q2, "q2_2026", "202604", "202606"))
OVERLAY_LOG = Path("docs/governance/overlay_log.json")
WEIGHTS = {"baseline": 0.60, "adverse": 0.30, "severely_adverse": 0.10}  # ADR-005
SEVERE_RANGE = (0.05, 0.10, 0.15, 0.20, 0.25)  # floor 5%, cap 25%
UPLIFT = 0.3434541551805488  # L-038 sizing (docs/phase4/overlay_sizing_L038.json): Option A sensitivity
LOSS_CODES = lgd.LOSS_CODES
FORECAST_MONTHS = scenarios.FORECAST_MONTHS  # ADR-003 settings; must match the projection job's parameters
REVERSION_MONTHS = scenarios.REVERSION_MONTHS


def cents(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def group_of(vintage: Any) -> Any:
    v = np.asarray(vintage, dtype=int)
    return np.where(
        v >= 2025,
        "2025-2026",
        np.where(v >= 2022, "2022-2024", np.where(v >= 2019, "2019-2021", "2018 and older")),
    )


def check_settings(book: Any, src: Path) -> None:
    """The projection job and this pricing must use the same forecast and reversion lengths: the default and
    payoff chances were simulated under the job's economy. Books written before the settings existed carry no
    columns and were built with ADR-003's 24 and 12."""
    job = tuple(
        int(book[c].iloc[0]) if c in book.columns else d
        for c, d in (("forecast_months", 24), ("reversion_months", 12))
    )
    if job != (FORECAST_MONTHS, REVERSION_MONTHS):
        raise SystemExit(f"{src}: projected with forecast/reversion {job}, priced with "
                         f"{(FORECAST_MONTHS, REVERSION_MONTHS)}; re-run the projection or change the settings")  # fmt: skip


def load_inputs(con: Any) -> dict[str, Any]:
    import pandas as pd

    with open(LOCAL / "phase3" / "lgd_model.pkl", "rb") as fh:
        model = pickle.load(fh)
    curtail = {int(k): v for k, v in json.loads((LOCAL / "phase3" / "ead_rates.json").read_text()).items()}
    L = LOCAL / "silver" / "macro"
    return {"lgd": model, "curtail": curtail, "state": pd.read_parquet(L / "state_monthly.parquet"),
            "national": pd.read_parquet(L / "national_monthly.parquet"), "fed": {y: pd.read_parquet(L / f"{t}.parquet") for y, t in scenarios.FED_TABLES.items()},
            "long_run": pd.read_parquet(L / "long_run_averages.parquet")}  # fmt: skip


def price_run(R: str, E: str, name: str, inp: dict[str, Any]) -> Any:
    """Each loan's lifetime expected loss under one scenario, the economy dated E, for the book at R."""
    import pandas as pd

    src, cache = RES / f"book_{R}_{E}_{name}.parquet", RES / f"priced_{R}_{E}_{name}.parquet"
    if cache.exists() and cache.stat().st_mtime > src.stat().st_mtime:
        return pd.read_parquet(cache)  # already priced from this exact projection file
    book = pd.read_parquet(src)
    check_settings(book, src)
    p_def = book[[f"pd_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
    p_pre = book[[f"pp_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
    E_book = (
        book
        if E == R
        else pd.read_parquet(RES / f"book_{E}_{E}_{name}.parquet", columns=["property_state", "upb_start"])
    )
    weights = E_book.groupby("property_state")["upb_start"].sum().to_dict()
    gap = multistate.months_between(E, R) - 1
    sc = scenarios.build(name, E, inp["state"], inp["national"], inp["fed"][scenarios.fed_year(E)], inp["long_run"], weights,
                         horizon=engine.MONTHLY + 3 + gap, forecast_months=FORECAST_MONTHS,
                         reversion_months=REVERSION_MONTHS)  # fmt: skip
    growth = sc.long_run["hpi_growth"]
    out = engine.price(book, p_def, p_pre, sc.macro, R, inp["lgd"], inp["curtail"], growth, FORECAST_MONTHS)
    frame = book[
        [
            "loan_id",
            "vintage_year",
            "property_state",
            "credit_score",
            "upb_start",
            "current_ltv",
            "first_state",
        ]
    ].copy()
    for k, v in out.items():
        frame[k] = v
    frame.to_parquet(cache, index=False)
    return frame


def defaulted_pipeline(con: Any, R: str, model: Any) -> Any:
    """Loans already in default at the end of R and not yet resolved: an ADR-002 first default on or before R,
    still 90+ days late or in REO (status 03 and above, or RA) at R, no zero balance yet. They have left the
    performing book (the engine), but a bank keeps a reserve on them until the loss is realised: balance at R
    x the Phase 3 LGD for the loan's inputs at default."""
    perf = f"read_parquet('{LOCAL}/bronze/freddie_perf/**/*.parquet', hive_partitioning=true)"
    ev = con.sql(
        f"select * from read_parquet('{LOCAL}/phase3/lgd_events.parquet') where default_ym <= '{R}'"
    ).df()
    rows = con.sql(f"""select loan_id, cast(current_upb as double) as balance, delinquency_status from {perf}
        where period = '{R}' and zero_balance_code is null
          and (delinquency_status = 'RA' or (delinquency_status <> 'XX' and try_cast(delinquency_status as int) >= 3))""").df()  # fmt: skip
    p = ev.merge(rows, on="loan_id", how="inner")
    p = p.dropna(subset=["ltv_default", "hpi_change_12"])
    p["lgd"] = model.predict(lgd.matrix(p))
    p["balance"] = p["balance"].where(p["balance"] > 0, p["default_upb"])
    p["reserve"] = p["balance"] * p["lgd"]
    return p


def tie_out(frame: Any) -> dict[str, Any]:
    loans = frame["total"].to_numpy(dtype=float)
    book = float(loans.sum())
    exact = math.fsum(loans.tolist())
    return {"book_reserve": book, "sum_of_loan_reserves": exact, "difference": book - exact,
            "passes": abs(book - exact) < 0.01, "loans": int(len(frame))}  # fmt: skip


def weighted(totals: dict[str, float], w: dict[str, float] = WEIGHTS) -> float:
    return sum(w[s] * totals[s] for s in w)


def severe_weights(sev: float) -> dict[str, float]:
    """The severe weight moved, baseline and adverse keep their 2:1 ratio (ADR-005 sensitivity)."""
    rest = 1.0 - sev
    return {"baseline": rest * 2 / 3, "adverse": rest / 3, "severely_adverse": sev}


def losses(con: Any, first: str, last: str, model: Any = None) -> Any:
    """Loss events with disposition in [first, last]: net write-off (loss, a gain counts as 0), recoveries
    (MI and other recoveries; Freddie stores them negative) and gross = net + recoveries.

    Freddie reports the loss about three months after the sale. With `model` (the Phase 3 LGD), a disposition
    whose loss is not reported yet gets an **estimated** write-off: the balance removed x the LGD for its
    inputs at default (what was reserved for it), no recoveries yet; flagged `estimated`, trued up at the next
    close. Without `model` such dispositions are left out (history: the benchmarks)."""
    perf = f"read_parquet('{LOCAL}/bronze/freddie_perf/**/*.parquet', hive_partitioning=true)"
    codes = ", ".join(f"'{c}'" for c in LOSS_CODES)
    d = con.sql(f"""
        select loan_id, zero_balance_date as disp_ym, actual_loss is null as estimated,
               cast(zero_balance_removal_upb as double) as removal_upb,
               greatest(coalesce(actual_loss, 0), 0) as net_write_off,
               -(coalesce(mi_recoveries, 0) + coalesce(non_mi_recoveries, 0)) as recoveries
        from {perf} where zero_balance_code in ({codes}) and zero_balance_date between '{first}' and '{last}'""").df()  # fmt: skip
    if model is None:
        d = d[~d["estimated"]].copy()
    elif d["estimated"].any():
        ev = con.sql(f"select * from read_parquet('{LOCAL}/phase3/lgd_events.parquet')").df()
        e = d[d["estimated"]].merge(ev, on="loan_id", how="left")
        ok = e.dropna(subset=["ltv_default", "hpi_change_12"])
        lgd_est = np.full(len(e), np.nan)
        lgd_est[ok.index.to_numpy()] = model.predict(lgd.matrix(ok))
        lgd_est = np.where(
            np.isnan(lgd_est), np.nanmean(lgd_est) if np.isfinite(lgd_est).any() else 0.0, lgd_est
        )
        d.loc[d["estimated"], "net_write_off"] = e["removal_upb"].to_numpy() * lgd_est
        d.loc[d["estimated"], "recoveries"] = 0.0
    return d.assign(gross_write_off=lambda x: x.net_write_off + x.recoveries)


def rollforward(start: Any, end: Any, wo: Any) -> dict[str, Any]:
    """One quarter: start (booked total allowance) + provision - gross write-offs + recoveries = end, to the
    cent, overall and by loan group (provision is the balancing figure, as in a bank's books)."""
    import pandas as pd

    s = start.groupby("group")["reserve"].sum()
    e = end.groupby("group")["reserve"].sum()
    w = wo.groupby("group")[["gross_write_off", "recoveries"]].sum()
    groups = sorted(set(s.index) | set(e.index) | set(w.index))
    rows, tot = [], {k: Decimal("0") for k in ("start", "write_offs", "recoveries", "provision", "end")}
    for g in groups:
        st, en = cents(float(s.get(g, 0.0))), cents(float(e.get(g, 0.0)))
        wr = cents(float(w["gross_write_off"].get(g, 0.0))) if g in w.index else Decimal("0")
        rc = cents(float(w["recoveries"].get(g, 0.0))) if g in w.index else Decimal("0")
        pv = en - st + wr - rc
        assert st + pv - wr + rc == en
        rows.append(
            {
                "group": g,
                "start": str(st),
                "provision": str(pv),
                "write_offs": str(-wr),
                "recoveries": str(rc),
                "end": str(en),
            }
        )
        for k, v in (("start", st), ("write_offs", wr), ("recoveries", rc), ("provision", pv), ("end", en)):
            tot[k] += v
    reconciles = tot["start"] + tot["provision"] - tot["write_offs"] + tot["recoveries"] == tot["end"]
    del pd
    return {"by_group": rows, "total": {k: str(-v if k == "write_offs" else v) for k, v in tot.items()},
            "reconciles_to_the_cent": bool(reconciles)}  # fmt: skip


def bridge(runs: dict[tuple[str, str, str], Any], a: str, b: str) -> dict[str, Any]:
    """Reserve at quarter-end `a` -> quarter-end `b` (weighted, performing loans), step by step:
    exits (loans in the `a` book not in `b`: their `a` reserve removed), new loans (`b` reserve of loans not
    in `a`), credit quality and ageing (continuing loans: `b` book under the `a` economy minus their `a`
    reserve), economic forecast (`b` economy minus `a` economy, `b` book; includes a change of Fed scenario
    year), model changes (none: the same v2 models). Overlays and the defaulted-loan reserve are added by
    the caller."""
    steps = {k: 0.0 for k in ("start", "exits", "new_loans", "credit_quality_and_ageing", "economic_forecast",
                              "model_changes", "overlays", "end")}  # fmt: skip
    for s, w in WEIGHTS.items():
        mar = runs[(a, a, s)].set_index("loan_id")["total"]
        jun = runs[(b, b, s)].set_index("loan_id")["total"]
        jun_marecon = runs[(b, a, s)].set_index("loan_id")["total"]
        cont = mar.index.intersection(jun.index)
        steps["start"] += w * float(mar.sum())
        steps["exits"] += -w * float(mar.drop(cont).sum())
        steps["new_loans"] += w * float(jun.drop(cont).sum())
        steps["credit_quality_and_ageing"] += w * float(jun_marecon[cont].sum() - mar[cont].sum())
        steps["economic_forecast"] += w * float(jun[cont].sum() - jun_marecon[cont].sum())
        steps["end"] += w * float(jun.sum())
    check = steps["start"] + sum(v for k, v in steps.items() if k not in ("start", "end"))
    return {"steps": steps, "reconciles": abs(check - steps["end"]) < 0.01}


def benchmarks(con: Any, book: Any, base: Any) -> dict[str, Any]:
    """Loss-rate method (long-run annual net loss rate x balance x expected remaining life) and vintage method
    (remaining part of the average cumulative loss curve of mature vintages, by the book's vintage age)."""
    panel = f"read_parquet('{LOCAL}/panel/**/*.parquet', hive_partitioning=true)"
    orig = f"read_parquet('{LOCAL}/bronze/freddie_orig/**/*.parquet', hive_partitioning=true)"
    lev = losses(con, "199901", "202512")
    lev["year"] = lev["disp_ym"].str[:4]
    bal = con.sql(f"select substr(period_ym, 1, 4) as year, sum(current_upb) / 12.0 as avg_balance "
                  f"from {panel} where period_ym <= '202512' group by 1").df().set_index("year")["avg_balance"]  # fmt: skip
    annual = lev.groupby("year")["net_write_off"].sum().reindex(bal.index).fillna(0.0) / bal
    rate = float(annual.mean())
    # expected remaining life (years) of the June book, baseline: survival-weighted, balance-weighted
    p = base
    pd_cols = [f"pd_{k}" for k in range(1, engine.MONTHLY + 1)]
    pp_cols = [f"pp_{k}" for k in range(1, engine.MONTHLY + 1)]
    exits = (p[pd_cols].to_numpy() + p[pp_cols].to_numpy()).cumsum(axis=1)
    surv36 = np.clip(1 - exits, 0, 1)
    _, h_d, h_p = engine.tail_hazards(p[pd_cols].to_numpy(), p[pp_cols].to_numpy())
    h = np.clip(h_d + h_p, 1e-6, 1)
    rem = np.maximum(p["remaining_start"].to_numpy(dtype=float) - engine.MONTHLY, 0)
    tail_months = surv36[:, -1] * (1 - (1 - h) ** rem) / h
    life_years = (surv36.sum(axis=1) + tail_months) / 12.0
    w = p["upb_start"].to_numpy(dtype=float)
    wal = float((life_years * w).sum() / w.sum())
    loss_rate_reserve = rate * float(w.sum()) * wal
    # vintage method
    lev_v = lev.merge(
        con.sql(
            f"select loan_id, cast(substr(first_payment_date, 1, 4) as int) as vintage_year from {orig}"
        ).df(),
        on="loan_id",
    )
    lev_v["age"] = lev_v["disp_ym"].str[:4].astype(int) - lev_v["vintage_year"]
    ob = (
        con.sql(
            f"select cast(substr(first_payment_date, 1, 4) as int) as vintage_year, sum(orig_upb) as orig_balance from {orig} group by 1"
        )
        .df()
        .set_index("vintage_year")["orig_balance"]
    )
    mature = [v for v in range(1999, 2013) if v in ob.index]
    curves = {}
    for v in mature:
        by_age = lev_v[lev_v.vintage_year == v].groupby("age")["net_write_off"].sum()
        curves[v] = np.cumsum([by_age.get(a, 0.0) for a in range(0, 28)]) / ob[v]
    W = np.array([ob[v] for v in mature])
    avg = np.array([sum(curves[v][a] * ob[v] for v in mature) / W.sum() for a in range(0, 28)])
    last_age = 2026 - 1999
    vint = {}
    for v, g in p.groupby("vintage_year"):
        age = max(2026 - int(v), 0)
        remaining_rate = float(avg[min(last_age, 27)] - avg[min(age, 27)])
        live_share = (
            float(g["upb_start"].sum() / ob.get(int(v), np.nan)) if int(v) in ob.index else float("nan")
        )
        vint[str(int(v))] = {"age": age, "remaining_cumulative_loss_rate": remaining_rate,
                             "orig_balance": float(ob.get(int(v), 0.0)), "book_balance": float(g["upb_start"].sum()),
                             "reserve": remaining_rate * float(ob.get(int(v), 0.0)) * (min(live_share, 1.0) if live_share == live_share else 0.0)}  # fmt: skip
    vintage_reserve = float(sum(x["reserve"] for x in vint.values()))
    return {"loss_rate_method": {"annual_net_loss_rate_1999_2025": rate, "annual_rates": {k: float(v) for k, v in annual.items()},
                                 "expected_remaining_life_years": wal, "book_balance": float(w.sum()), "reserve": loss_rate_reserve},
            "vintage_method": {"mature_vintages": mature, "average_cumulative_loss_curve": avg.tolist(),
                               "by_vintage": vint, "reserve": vintage_reserve,
                               "note": "remaining part of the average curve x the vintage's original balance x the share still on the book"}}  # fmt: skip


def disclosures(book: Any, wo_ytd: Any, wo_q2: Any, orig_year: Any) -> dict[str, Any]:
    fico = np.select([book.credit_score < 620, book.credit_score < 680, book.credit_score < 740, book.credit_score >= 740],
                     ["<620", "620-679", "680-739", "740+"], default="missing")  # fmt: skip
    ltv = book["current_ltv"].to_numpy(dtype=float)
    ltvb = np.select(
        [ltv <= 60, ltv <= 80, ltv <= 90, ltv <= 100, ltv > 100],
        ["<=60", "60-80", "80-90", "90-100", ">100"],
        default="missing",
    )
    b = book.assign(fico_band=fico, ltv_band=ltvb)

    def table(col: str) -> dict[str, dict[str, float]]:
        t = b.pivot_table(
            index=col, columns="vintage_year", values="upb_start", aggfunc="sum", fill_value=0.0
        )
        return {str(i): {str(int(c)): float(t.loc[i, c]) for c in t.columns} for i in t.index}

    def wo(d: Any) -> dict[str, float]:
        d = d.merge(orig_year, on="loan_id", how="left")
        return {str(int(k)): float(v) for k, v in d.groupby("vintage_year")["gross_write_off"].sum().items()}

    return {"credit_quality_fico_by_origination_year": table("fico_band"),
            "credit_quality_current_ltv_by_origination_year": table("ltv_band"),
            "gross_write_offs_by_origination_year_q2_2026": wo(wo_q2),
            "gross_write_offs_by_origination_year_ytd_2026": wo(wo_ytd)}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    environment.current_env()
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    inp = load_inputs(con)
    runs: dict[tuple[str, str, str], Any] = {}
    for R, E in ((Q0, Q0), (Q1, Q1), (Q2, Q2), (Q1, Q0), (Q2, Q1)):
        for s in scenarios.SCENARIOS:
            runs[(R, E, s)] = price_run(R, E, s, inp)
            print(R, E, s, f"{runs[(R, E, s)]['total'].sum():,.0f}", flush=True)
    res: dict[str, Any] = {"note": "Aggregates only. Educational model on Freddie Mac conforming mortgages (sample book); not an "
                                   "audited bank reserve and not accounting advice.",
                           "model": "PD/prepayment M-01 v2 @challenger (holdout failed, L-038; check bias L-039); LGD M-02; EAD M-03",
                           "overlay_decision": "Option B: no L-038 overlay on the baseline reserve (L-033 netting decision, 19 Nov 2026)",
                           "uplift_sensitivity": UPLIFT, "weights": WEIGHTS,
                           "forecast_months": FORECAST_MONTHS, "reversion_months": REVERSION_MONTHS, "dates": {}}  # fmt: skip
    pipelines: dict[str, Any] = {}
    log, assessed = overlays.load(OVERLAY_LOG)
    for R in (Q0, Q1, Q2):
        d: dict[str, Any] = {"scenarios": {}, "tie_outs": {}, "fed_scenario_year": scenarios.fed_year(R)}
        for s in scenarios.SCENARIOS:
            f = runs[(R, R, s)]
            d["tie_outs"][s] = tie_out(f)
            d["scenarios"][s] = {"reserve": float(f["total"].sum()), "forecast": float(f["forecast"].sum()),
                                 "reversion": float(f["reversion"].sum()), "tail": float(f["tail"].sum()),
                                 "lifetime_pd_weighted_by_balance": float((f["lifetime_pd"] * f["upb_start"]).sum() / f["upb_start"].sum()),
                                 "reserve_with_uplift": float(f["total"].sum()) * (1 + UPLIFT)}  # fmt: skip
        tot = {s: d["scenarios"][s]["reserve"] for s in scenarios.SCENARIOS}
        bal = float(runs[(R, R, "baseline")]["upb_start"].sum())
        d["book"] = {"loans": int(len(runs[(R, R, "baseline")])), "balance": bal}
        d["weighted_reserve"] = weighted(tot)
        d["weighted_reserve_with_uplift"] = weighted(tot) * (1 + UPLIFT)
        d["reserve_ratio"] = d["weighted_reserve"] / bal
        d["weight_sensitivity"] = [{"severe_weight": sv, "reserve": weighted(tot, severe_weights(sv)),
                                    "reserve_with_uplift": weighted(tot, severe_weights(sv)) * (1 + UPLIFT)} for sv in SEVERE_RANGE]  # fmt: skip
        d["severely_adverse_with_and_without_uplift"] = {
            "without": tot["severely_adverse"],
            "with": tot["severely_adverse"] * (1 + UPLIFT),
        }
        pipe = defaulted_pipeline(con, R, inp["lgd"])
        pipelines[R] = pipe
        d["defaulted_pipeline"] = {"loans": int(len(pipe)), "balance": float(pipe["balance"].sum()),
                                   "reserve": float(pipe["reserve"].sum()),
                                   "lgd": float(pipe["reserve"].sum() / max(pipe["balance"].sum(), 1e-9))}  # fmt: skip
        # ADR-006: the register checks every overlay against the modelled (weighted) reserve; only booked ones count
        d["overlays"] = overlays.register(log, d["weighted_reserve"], R)
        d["overlays"]["assessments"] = [a for a in assessed if a.get("reserve_ym") == R]
        booked_overlays = d["overlays"]["booked_total"]
        d["total_allowance"] = d["weighted_reserve"] + booked_overlays + d["defaulted_pipeline"]["reserve"]
        d["total_allowance_with_uplift"] = (
            d["weighted_reserve_with_uplift"] + booked_overlays + d["defaulted_pipeline"]["reserve"]
        )
        d["total_allowance_ratio"] = d["total_allowance"] / (bal + d["defaulted_pipeline"]["balance"])
        res["dates"][R] = d
    # rollforward Q2 2026: total allowance (performing, weighted + defaulted pipeline); every loss realised in Q2
    orig_year = con.sql("select loan_id, cast(substr(first_payment_date, 1, 4) as int) as vintage_year from "
                        f"read_parquet('{LOCAL}/bronze/freddie_orig/**/*.parquet', hive_partitioning=true)").df()  # fmt: skip

    def booked(R: str) -> Any:
        f = runs[(R, R, "baseline")][["loan_id", "vintage_year"]].copy()
        f["reserve"] = sum(WEIGHTS[s] * runs[(R, R, s)]["total"].to_numpy() for s in WEIGHTS)
        pipe = pipelines[R][["loan_id", "reserve"]].merge(orig_year, on="loan_id", how="left")
        f = pd.concat([f, pipe[["loan_id", "vintage_year", "reserve"]]], ignore_index=True)
        f["group"] = group_of(f["vintage_year"].fillna(2000))
        ov_total = res["dates"][R]["overlays"]["booked_total"]
        if ov_total:  # booked overlays sit at book level, in their own rollforward row
            f = pd.concat([f, pd.DataFrame([{"loan_id": "", "vintage_year": None, "reserve": ov_total,
                                             "group": "Overlays (ADR-006)"}])], ignore_index=True)  # fmt: skip
        return f

    for a, b_, tag, first_m, last_m in QUARTERS:
        start, end = booked(a), booked(b_)
        wo = losses(con, first_m, last_m, inp["lgd"])
        wo = wo.merge(orig_year, on="loan_id", how="left")  # every loss realised in the quarter
        wo["group"] = group_of(wo["vintage_year"].fillna(2000))
        rf = rollforward(start, end, wo)
        rf["write_off_events"] = int(len(wo))
        rf["estimated_write_off_events"] = int(wo["estimated"].sum())
        rf["note"] = (
            "Freddie reports losses about 3 months after the sale; write-offs not yet reported are estimated "
            "(balance removed x Phase 3 LGD) and trued up when reported"
        )
        res[f"rollforward_{tag}"] = rf
        br = bridge(runs, a, b_)
        steps = br["steps"]
        steps["overlays"] = (
            res["dates"][b_]["overlays"]["booked_total"] - res["dates"][a]["overlays"]["booked_total"]
        )
        steps["start"] += (
            res["dates"][a]["defaulted_pipeline"]["reserve"] + res["dates"][a]["overlays"]["booked_total"]
        )
        steps["defaulted_loan_reserve_change"] = (
            res["dates"][b_]["defaulted_pipeline"]["reserve"]
            - res["dates"][a]["defaulted_pipeline"]["reserve"]
        )
        steps["end"] += (
            res["dates"][b_]["defaulted_pipeline"]["reserve"] + res["dates"][b_]["overlays"]["booked_total"]
        )
        steps["end"] = steps.pop("end")  # keep "end" last
        total = steps["start"] + sum(v for k, v in steps.items() if k not in ("start", "end"))
        br["reconciles"] = abs(total - steps["end"]) < 0.01
        br["ties_to_total_allowance"] = (
            abs(steps["start"] - res["dates"][a]["total_allowance"]) < 0.01
            and abs(steps["end"] - res["dates"][b_]["total_allowance"]) < 0.01
        )
        br["fed_scenario_years"] = [
            res["dates"][a]["fed_scenario_year"],
            res["dates"][b_]["fed_scenario_year"],
        ]
        res["bridge_q4_2025_to_q1_2026" if tag == "q1_2026" else "bridge_q1_to_q2_2026"] = br
        if tag == "q2_2026":
            wo_q2 = wo
    end = booked(Q2)
    res["benchmarks"] = benchmarks(con, end, pd.read_parquet(RES / f"book_{Q2}_{Q2}_baseline.parquet"))
    wo_ytd = losses(con, "202601", "202606", inp["lgd"])
    res["disclosures"] = disclosures(
        runs[(Q2, Q2, "baseline")], wo_ytd, wo_q2.drop(columns=["vintage_year", "group"]), orig_year
    )
    res["disclosures"]["rollforward_by_loan_group"] = {
        "q1_2026": res["rollforward_q1_2026"]["by_group"],
        "q2_2026": res["rollforward_q2_2026"]["by_group"],
    }
    print(
        json.dumps(
            {
                R: {k: res["dates"][R][k] for k in ("book", "weighted_reserve", "reserve_ratio")}
                | {
                    "scenarios": {s: round(v["reserve"]) for s, v in res["dates"][R]["scenarios"].items()},
                    "tie_outs": {s: t["passes"] for s, t in res["dates"][R]["tie_outs"].items()},
                }
                for R in res["dates"]
            },
            indent=1,
            default=float,
        )
    )
    print(json.dumps({"rollforward_q1": res["rollforward_q1_2026"]["total"], "reconciles_q1": res["rollforward_q1_2026"]["reconciles_to_the_cent"],
                      "bridge_q1": res["bridge_q4_2025_to_q1_2026"],
                      "rollforward": res["rollforward_q2_2026"]["total"], "reconciles": res["rollforward_q2_2026"]["reconciles_to_the_cent"],
                      "bridge": res["bridge_q1_to_q2_2026"], "loss_rate": res["benchmarks"]["loss_rate_method"]["reserve"],
                      "vintage": res["benchmarks"]["vintage_method"]["reserve"]}, indent=1, default=float))  # fmt: skip
    if args.write:
        OUT.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
