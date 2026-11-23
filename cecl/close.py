"""The quarterly close (Phase 8): price, weight, roll forward and explain the allowance for any quarter-end.

The calculations of `scripts/build_reserve.py` (Phase 4, 30 June 2026), made general so that the same code
closes any quarter, on the PC (DuckDB over the local Parquet files) or on Databricks (the prod close job,
DuckDB over small frames that Spark pre-filters from the prod tables). Only the *relations* differ: every
function that reads loan data takes the DuckDB connection and the relation names (`rel["perf"]`,
`rel["orig"]`, `rel["events"]`), so dev and prod run the same SQL.

A close at quarter-end R, with the prior quarter-end P:
1. each scenario's book at R priced under the economy at R, and the R book under the P economy (bridge);
2. the weighted reserve (ADR-005, 60/30/10), the defaulted-loan reserve and the booked overlays (ADR-006);
3. the rollforward P -> R from the booked frame saved at P (start) to the booked frame at R (end), with the
   write-offs and recoveries realised in the quarter: provision is the balancing figure, to the cent;
4. the bridge P -> R (exits, new loans, credit quality and ageing, economic forecast, model changes,
   overlays, defaulted-loan reserve);
5. disclosure tables and the benchmarks (loss-rate and vintage methods).

Aggregates only leave this module's callers: the loan-level frames stay on the PC or in the private volume.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np

from cecl import engine, lgd, scenarios
from cecl.models import multistate

WEIGHTS = {"baseline": 0.60, "adverse": 0.30, "severely_adverse": 0.10}  # ADR-005
SEVERE_RANGE = (0.05, 0.10, 0.15, 0.20, 0.25)  # floor 5%, cap 25%
UPLIFT = 0.3434541551805488  # L-038 sizing (docs/phase4/overlay_sizing_L038.json): Option A sensitivity
LOSS_CODES = lgd.LOSS_CODES
FORECAST_MONTHS = scenarios.FORECAST_MONTHS  # ADR-003 settings; must match the projection job's parameters
REVERSION_MONTHS = scenarios.REVERSION_MONTHS
BOOKED_COLUMNS = ["loan_id", "vintage_year", "group", "reserve"]


# ---------------------------------------------------------------- quarters


def quarter_end_ym(d: date) -> str:
    """The quarter-end month (YYYYMM) of the quarter that contains `d`."""
    return f"{d.year}{(((d.month - 1) // 3) + 1) * 3:02d}"


def is_quarter_end(ym: str) -> bool:
    return len(ym) == 6 and ym.isdigit() and ym[4:] in ("03", "06", "09", "12")


def prior_quarter(ym: str) -> str:
    """The quarter-end before `ym` (e.g. 202609 -> 202606, 202603 -> 202512)."""
    if not is_quarter_end(ym):
        raise ValueError(f"{ym!r} is not a quarter-end month")
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y - 1}12" if m == 3 else f"{y}{m - 3:02d}"


def next_quarter(ym: str) -> str:
    """The quarter-end after `ym` (e.g. 202606 -> 202609, 202612 -> 202703)."""
    if not is_quarter_end(ym):
        raise ValueError(f"{ym!r} is not a quarter-end month")
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y + 1}03" if m == 12 else f"{y}{m + 3:02d}"


def quarter_months(ym: str) -> tuple[str, str]:
    """First and last month of the quarter ending `ym`."""
    if not is_quarter_end(ym):
        raise ValueError(f"{ym!r} is not a quarter-end month")
    return f"{ym[:4]}{int(ym[4:]) - 2:02d}", ym


def quarter_tag(ym: str) -> str:
    """'q2_2026' for 202606."""
    if not is_quarter_end(ym):
        raise ValueError(f"{ym!r} is not a quarter-end month")
    return f"q{int(ym[4:]) // 3}_{ym[:4]}"


# ---------------------------------------------------------------- small helpers


def cents(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def group_of(vintage: Any) -> Any:
    v = np.asarray(vintage, dtype=int)
    return np.where(
        v >= 2025,
        "2025-2026",
        np.where(v >= 2022, "2022-2024", np.where(v >= 2019, "2019-2021", "2018 and older")),
    )


def check_settings(
    book: Any, src: Any, forecast: int = FORECAST_MONTHS, reversion: int = REVERSION_MONTHS
) -> None:
    """The projection job and the pricing must use the same forecast and reversion lengths: the default and
    payoff chances were simulated under the job's economy. Books written before the settings existed carry no
    columns and were built with ADR-003's 24 and 12."""
    job = tuple(
        int(book[c].iloc[0]) if c in book.columns else d
        for c, d in (("forecast_months", 24), ("reversion_months", 12))
    )
    if job != (forecast, reversion):
        raise ValueError(f"{src}: projected with forecast/reversion {job}, priced with "
                         f"{(forecast, reversion)}; re-run the projection or change the settings")  # fmt: skip


def tie_out(frame: Any) -> dict[str, Any]:
    """Control C02: the book reserve equals the sum of the loan reserves."""
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


# ---------------------------------------------------------------- pricing


def price(book: Any, R: str, E: str, name: str, inp: dict[str, Any], e_weights: dict[str, float]) -> Any:
    """Each loan's lifetime expected loss under one scenario (economy dated E) for the book at R.
    `inp`: lgd (model), curtail, state, national, fed {year: paths}, long_run. `e_weights`: the E book's
    balance by state (the scenario's state mix)."""
    p_def = book[[f"pd_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
    p_pre = book[[f"pp_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
    gap = multistate.months_between(E, R) - 1
    sc = scenarios.build(name, E, inp["state"], inp["national"], inp["fed"][scenarios.fed_year(E)], inp["long_run"],
                         e_weights, horizon=engine.MONTHLY + 3 + gap, forecast_months=FORECAST_MONTHS,
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
    return frame


def state_weights(book: Any) -> dict[str, float]:
    return dict(book.groupby("property_state")["upb_start"].sum().to_dict())


# ---------------------------------------------------------------- loan data (same SQL on the PC and Databricks)


def defaulted_pipeline(con: Any, rel: dict[str, str], R: str, model: Any) -> Any:
    """Loans already in default at the end of R and not yet resolved: an ADR-002 first default on or before R,
    still 90+ days late or in REO (status 03 and above, or RA) at R, no zero balance yet. They have left the
    performing book (the engine), but a bank keeps a reserve on them until the loss is realised: balance at R
    x the Phase 3 LGD for the loan's inputs at default."""
    ev = con.sql(f"select * from {rel['events']} where default_ym <= '{R}'").df()
    rows = con.sql(f"""select loan_id, cast(current_upb as double) as balance, delinquency_status from {rel['perf']}
        where period = '{R}' and zero_balance_code is null
          and (delinquency_status = 'RA' or (delinquency_status <> 'XX' and try_cast(delinquency_status as int) >= 3))""").df()  # fmt: skip
    p = ev.merge(rows, on="loan_id", how="inner")
    p = p.dropna(subset=["ltv_default", "hpi_change_12"])
    p["lgd"] = model.predict(lgd.matrix(p)) if len(p) else np.zeros(0)
    p["balance"] = p["balance"].where(p["balance"] > 0, p["default_upb"])
    p["reserve"] = p["balance"] * p["lgd"]
    return p


def losses(con: Any, rel: dict[str, str], first: str, last: str, model: Any = None) -> Any:
    """Loss events with disposition in [first, last]: net write-off (loss, a gain counts as 0), recoveries
    (MI and other recoveries; Freddie stores them negative) and gross = net + recoveries.

    Freddie reports the loss about three months after the sale. With `model` (the Phase 3 LGD), a disposition
    whose loss is not reported yet gets an **estimated** write-off: the balance removed x the LGD for its
    inputs at default (what was reserved for it), no recoveries yet; flagged `estimated`, trued up at the next
    close. Without `model` such dispositions are left out (history: the benchmarks)."""
    codes = ", ".join(f"'{c}'" for c in LOSS_CODES)
    d = con.sql(f"""
        select loan_id, zero_balance_date as disp_ym, actual_loss is null as estimated,
               cast(zero_balance_removal_upb as double) as removal_upb,
               greatest(coalesce(actual_loss, 0), 0) as net_write_off,
               -(coalesce(mi_recoveries, 0) + coalesce(non_mi_recoveries, 0)) as recoveries
        from {rel['perf']} where zero_balance_code in ({codes}) and zero_balance_date between '{first}' and '{last}'""").df()  # fmt: skip
    if model is None:
        d = d[~d["estimated"]].copy()
    elif d["estimated"].any():
        ev = con.sql(f"select * from {rel['events']}").df()
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


# ---------------------------------------------------------------- one reserve date


def date_block(frames: dict[str, Any], pipe: Any, R: str, overlay_register: dict[str, Any],
               assessments: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    """Everything about the reserve at R: per-scenario reserves and tie-outs, the weighted reserve, weight
    sensitivity, the uplift sensitivity, the defaulted-loan reserve, overlays and the total allowance.
    `frames`: the priced R book per scenario under the R economy. `overlay_register`: `overlays.register`
    for R (only approved overlays that pass every rule are booked)."""
    d: dict[str, Any] = {"scenarios": {}, "tie_outs": {}, "fed_scenario_year": scenarios.fed_year(R)}
    for s in scenarios.SCENARIOS:
        f = frames[s]
        d["tie_outs"][s] = tie_out(f)
        d["scenarios"][s] = {"reserve": float(f["total"].sum()), "forecast": float(f["forecast"].sum()),
                             "reversion": float(f["reversion"].sum()), "tail": float(f["tail"].sum()),
                             "lifetime_pd_weighted_by_balance": float((f["lifetime_pd"] * f["upb_start"]).sum() / f["upb_start"].sum()),
                             "reserve_with_uplift": float(f["total"].sum()) * (1 + UPLIFT)}  # fmt: skip
    tot = {s: d["scenarios"][s]["reserve"] for s in scenarios.SCENARIOS}
    bal = float(frames["baseline"]["upb_start"].sum())
    d["book"] = {"loans": int(len(frames["baseline"])), "balance": bal}
    d["weighted_reserve"] = weighted(tot)
    d["weighted_reserve_with_uplift"] = weighted(tot) * (1 + UPLIFT)
    d["reserve_ratio"] = d["weighted_reserve"] / bal
    d["weight_sensitivity"] = [{"severe_weight": sv, "reserve": weighted(tot, severe_weights(sv)),
                                "reserve_with_uplift": weighted(tot, severe_weights(sv)) * (1 + UPLIFT)} for sv in SEVERE_RANGE]  # fmt: skip
    d["severely_adverse_with_and_without_uplift"] = {
        "without": tot["severely_adverse"],
        "with": tot["severely_adverse"] * (1 + UPLIFT),
    }
    d["defaulted_pipeline"] = {"loans": int(len(pipe)), "balance": float(pipe["balance"].sum()),
                               "reserve": float(pipe["reserve"].sum()),
                               "lgd": float(pipe["reserve"].sum() / max(pipe["balance"].sum(), 1e-9))}  # fmt: skip
    d["overlays"] = dict(overlay_register)
    d["overlays"]["assessments"] = [a for a in assessments if a.get("reserve_ym") == R]
    booked_overlays = d["overlays"]["booked_total"]
    d["total_allowance"] = d["weighted_reserve"] + booked_overlays + d["defaulted_pipeline"]["reserve"]
    d["total_allowance_with_uplift"] = (
        d["weighted_reserve_with_uplift"] + booked_overlays + d["defaulted_pipeline"]["reserve"]
    )
    d["total_allowance_ratio"] = d["total_allowance"] / (bal + d["defaulted_pipeline"]["balance"])
    return d


def booked_frame(frames: dict[str, Any], pipe: Any, orig_year: Any, overlay_total: float) -> Any:
    """The booked allowance at a quarter-end, loan by loan (weighted performing reserve, then the defaulted
    loans), with each loan's group; booked overlays sit at book level in their own row. Saved at every close:
    it is the next close's rollforward start."""
    import pandas as pd

    f = frames["baseline"][["loan_id", "vintage_year"]].copy()
    f["reserve"] = sum(WEIGHTS[s] * frames[s]["total"].to_numpy() for s in WEIGHTS)
    p = pipe[["loan_id", "reserve"]].merge(orig_year, on="loan_id", how="left")
    f = pd.concat([f, p[["loan_id", "vintage_year", "reserve"]]], ignore_index=True)
    f["group"] = group_of(f["vintage_year"].fillna(2000))
    if overlay_total:
        f = pd.concat([f, pd.DataFrame([{"loan_id": "", "vintage_year": None, "reserve": overlay_total,
                                         "group": "Overlays (ADR-006)"}])], ignore_index=True)  # fmt: skip
    return f[BOOKED_COLUMNS]


# ---------------------------------------------------------------- rollforward and bridge


def rollforward(start: Any, end: Any, wo: Any) -> dict[str, Any]:
    """One quarter: start (booked total allowance) + provision - gross write-offs + recoveries = end, to the
    cent, overall and by loan group (provision is the balancing figure, as in a bank's books)."""
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
        rows.append({"group": g, "start": str(st), "provision": str(pv), "write_offs": str(-wr),
                     "recoveries": str(rc), "end": str(en)})  # fmt: skip
        for k, v in (("start", st), ("write_offs", wr), ("recoveries", rc), ("provision", pv), ("end", en)):
            tot[k] += v
    reconciles = tot["start"] + tot["provision"] - tot["write_offs"] + tot["recoveries"] == tot["end"]
    return {"by_group": rows, "total": {k: str(-v if k == "write_offs" else v) for k, v in tot.items()},
            "reconciles_to_the_cent": bool(reconciles)}  # fmt: skip


def bridge(
    runs: dict[tuple[str, str, str], Any], a: str, b: str, weights: dict[str, float] = WEIGHTS
) -> dict[str, Any]:
    """Reserve at quarter-end `a` -> quarter-end `b` (weighted, performing loans), step by step:
    exits (loans in the `a` book not in `b`: their `a` reserve removed), new loans (`b` reserve of loans not
    in `a`), credit quality and ageing (continuing loans: `b` book under the `a` economy minus their `a`
    reserve), economic forecast (`b` economy minus `a` economy, `b` book; includes a change of Fed scenario
    year), model changes (none: the same v2 models). Overlays and the defaulted-loan reserve are added by
    `finish_bridge`."""
    steps = {k: 0.0 for k in ("start", "exits", "new_loans", "credit_quality_and_ageing", "economic_forecast",
                              "model_changes", "overlays", "end")}  # fmt: skip
    for s, w in weights.items():
        old = runs[(a, a, s)].set_index("loan_id")["total"]
        new = runs[(b, b, s)].set_index("loan_id")["total"]
        new_old_economy = runs[(b, a, s)].set_index("loan_id")["total"]
        cont = old.index.intersection(new.index)
        steps["start"] += w * float(old.sum())
        steps["exits"] += -w * float(old.drop(cont).sum())
        steps["new_loans"] += w * float(new.drop(cont).sum())
        steps["credit_quality_and_ageing"] += w * float(new_old_economy[cont].sum() - old[cont].sum())
        steps["economic_forecast"] += w * float(new[cont].sum() - new_old_economy[cont].sum())
        steps["end"] += w * float(new.sum())
    check = steps["start"] + sum(v for k, v in steps.items() if k not in ("start", "end"))
    return {"steps": steps, "reconciles": abs(check - steps["end"]) < 0.01}


def finish_bridge(br: dict[str, Any], da: dict[str, Any], db: dict[str, Any]) -> dict[str, Any]:
    """Adds the overlays and the defaulted-loan reserve to a performing-loan bridge, so it runs from one
    total allowance to the next; checks it reconciles and ties to both totals."""
    steps = br["steps"]
    steps["overlays"] = db["overlays"]["booked_total"] - da["overlays"]["booked_total"]
    steps["start"] += da["defaulted_pipeline"]["reserve"] + da["overlays"]["booked_total"]
    steps["defaulted_loan_reserve_change"] = (
        db["defaulted_pipeline"]["reserve"] - da["defaulted_pipeline"]["reserve"]
    )
    steps["end"] += db["defaulted_pipeline"]["reserve"] + db["overlays"]["booked_total"]
    steps["end"] = steps.pop("end")  # keep "end" last
    total = steps["start"] + sum(v for k, v in steps.items() if k not in ("start", "end"))
    br["reconciles"] = abs(total - steps["end"]) < 0.01
    br["ties_to_total_allowance"] = (
        abs(steps["start"] - da["total_allowance"]) < 0.01
        and abs(steps["end"] - db["total_allowance"]) < 0.01
    )
    br["fed_scenario_years"] = [da["fed_scenario_year"], db["fed_scenario_year"]]
    return br


# ---------------------------------------------------------------- benchmarks and disclosures


def benchmarks(
    lev: Any, balance_by_year: Any, orig_vintage: Any, ob: Any, base: Any, R: str
) -> dict[str, Any]:
    """Loss-rate method (long-run annual net loss rate x balance x expected remaining life) and vintage method
    (remaining part of the average cumulative loss curve of mature vintages, by the book's vintage age).

    `lev`: realised loss events from 1999 to the end of the year before R (`losses(..., model=None)`);
    `balance_by_year`: average balance by calendar year (Series indexed by 'YYYY'); `orig_vintage`: loan_id ->
    vintage_year; `ob`: original balance by vintage (Series); `base`: the R book's baseline projection."""
    year = int(R[:4])
    lev = lev.copy()
    lev["year"] = lev["disp_ym"].str[:4]
    bal = balance_by_year
    annual = lev.groupby("year")["net_write_off"].sum().reindex(bal.index).fillna(0.0) / bal
    rate = float(annual.mean())
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
    lev_v = lev.merge(orig_vintage, on="loan_id")
    lev_v["age"] = lev_v["disp_ym"].str[:4].astype(int) - lev_v["vintage_year"]
    mature = [v for v in range(1999, 2013) if v in ob.index]
    curves = {}
    for v in mature:
        by_age = lev_v[lev_v.vintage_year == v].groupby("age")["net_write_off"].sum()
        curves[v] = np.cumsum([by_age.get(a, 0.0) for a in range(0, 28)]) / ob[v]
    W = np.array([ob[v] for v in mature])
    avg = np.array([sum(curves[v][a] * ob[v] for v in mature) / W.sum() for a in range(0, 28)])
    last_age = year - 1999
    vint = {}
    for v, g in p.groupby("vintage_year"):
        age = max(year - int(v), 0)
        remaining_rate = float(avg[min(last_age, 27)] - avg[min(age, 27)])
        live_share = (
            float(g["upb_start"].sum() / ob.get(int(v), np.nan)) if int(v) in ob.index else float("nan")
        )
        vint[str(int(v))] = {"age": age, "remaining_cumulative_loss_rate": remaining_rate,
                             "orig_balance": float(ob.get(int(v), 0.0)), "book_balance": float(g["upb_start"].sum()),
                             "reserve": remaining_rate * float(ob.get(int(v), 0.0)) * (min(live_share, 1.0) if live_share == live_share else 0.0)}  # fmt: skip
    vintage_reserve = float(sum(x["reserve"] for x in vint.values()))
    return {"loss_rate_method": {f"annual_net_loss_rate_1999_{year - 1}": rate, "annual_rates": {k: float(v) for k, v in annual.items()},
                                 "expected_remaining_life_years": wal, "book_balance": float(w.sum()), "reserve": loss_rate_reserve},
            "vintage_method": {"mature_vintages": mature, "average_cumulative_loss_curve": avg.tolist(),
                               "by_vintage": vint, "reserve": vintage_reserve,
                               "note": "remaining part of the average curve x the vintage's original balance x the share still on the book"}}  # fmt: skip


def disclosures(book: Any, wo_ytd: Any, wo_q: Any, orig_year: Any, R: str) -> dict[str, Any]:
    """10-Q style tables (aggregates only): credit quality (FICO and current-LTV bands) by origination year;
    gross write-offs by origination year, for the quarter and the year to date."""
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
            f"gross_write_offs_by_origination_year_{quarter_tag(R)}": wo(wo_q),
            f"gross_write_offs_by_origination_year_ytd_{R[:4]}": wo(wo_ytd)}  # fmt: skip


# ---------------------------------------------------------------- reserve adequacy (rolling backtest, live)


def reserve_adequacy(
    history: list[dict[str, Any]], realised: dict[str, float], R: str
) -> list[dict[str, Any]]:
    """The live part of the rolling backtest (roadmap v2 item 2): for every past booked quarter-end, the
    allowance it held and the net write-offs realised since then on the loans it covered, up to R.
    `history`: [{"reserve_ym", "total_allowance"}]; `realised`: reserve_ym -> net write-offs since then.
    A lifetime reserve is only fully tested once the book has run off, so `share_of_reserve_used` is shown
    with the months elapsed, never judged early."""
    out = []
    for h in sorted(history, key=lambda x: x["reserve_ym"]):
        ym = h["reserve_ym"]
        if ym >= R:
            continue
        used = float(realised.get(ym, 0.0))
        out.append({"reserve_ym": ym, "total_allowance": float(h["total_allowance"]),
                    "months_elapsed": multistate.months_between(ym, R) - 1, "net_write_offs_since": used,
                    "share_of_reserve_used": used / h["total_allowance"] if h["total_allowance"] else None})  # fmt: skip
    return out
