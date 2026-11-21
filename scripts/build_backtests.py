"""Phase 6 backtests on the PC: refit the LGD per date, price each projection, score against actuals.

    CECL_ENV=local python -m scripts.build_backtests --test crisis|rolling|forbearance [--write]

Inputs (git-ignored, loan-level): the projected books from the Databricks job `backtests`
(data/local/backtest/book_<tag>.parquet, downloaded here if missing; the volume keeps them), the local
panel, the Phase 3 loss events (`data/local/phase3/lgd_events.parquet`) and the booked LGD (only to value
defaults still unresolved by June 2026) and EAD rates. Outputs (aggregates only): docs/phase6/backtests.json,
one section per test. The rules are the ones committed before the tests ran (docs/phase6/test_designs.md):
- projected chances counted up to censoring only (L-039);
- actual loss of a default: Freddie's reported net loss (zero for a gain); unresolved defaults valued at the
  booked Phase 3 LGD, with the share of loss valued that way reported;
- the LGD for each date refitted (Phase 3 two-step recipe) on defaults whose loss was reported by that date.
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from cecl import backtest, ead, engine, environment, lgd
from cecl.models import multistate

LOCAL = Path("data/local")
BOOKS = LOCAL / "backtest"
OUT = Path("docs/phase6/backtests.json")
VOLUME = "dbfs:/Volumes/cecl_lab_dev/bronze/raw/results/backtest"
PANEL = f"read_parquet('{LOCAL}/panel/**/*.parquet', hive_partitioning=true)"
ROLLING_DATES = tuple(f"{y}12" for y in range(2012, 2023))
NOTE = ("Aggregates only. Educational model on Freddie Mac conforming mortgages (sample files); not an audited "
        "reserve. Model v2 @challenger failed its holdout (L-038); the holdout check was biased (L-039); no "
        "L-038 overlay (Option B). Information for the validator: no booked figure changes.")  # fmt: skip


def fetch_book(tag: str) -> Any:
    import pandas as pd

    path = BOOKS / f"book_{tag}.parquet"
    if not path.exists():
        BOOKS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["databricks", "fs", "cp", f"{VOLUME}/book_{tag}.parquet", str(path), "--profile", "cecl",
                        "--overwrite"], check=True, capture_output=True)  # fmt: skip
    return pd.read_parquet(path)


def inputs() -> dict[str, Any]:
    import pandas as pd

    macro_dir = LOCAL / "silver" / "macro"
    with open(LOCAL / "phase3" / "lgd_model.pkl", "rb") as fh:
        booked_lgd = pickle.load(fh)
    curtail = {int(k): v for k, v in json.loads((LOCAL / "phase3" / "ead_rates.json").read_text()).items()}
    actual = multistate.Macro.from_tables(pd.read_parquet(macro_dir / "state_monthly.parquet"),
                                          pd.read_parquet(macro_dir / "national_monthly.parquet"))  # fmt: skip
    events = pd.read_parquet(LOCAL / "phase3" / "lgd_events.parquet")
    return {"booked_lgd": booked_lgd, "curtail": curtail, "actual": actual, "events": events}


def refit_lgd(events: Any, as_of: str) -> tuple[Any, dict[str, Any]]:
    """The Phase 3 two-step LGD recipe fitted on defaults whose loss Freddie had reported by `as_of`."""
    usable = events[events.severity.notna() & (events.disp_ym <= as_of)].dropna(
        subset=["ltv_default", "hpi_change_12"]
    )
    model = lgd.TwoStepLGD().fit(usable)
    sev = np.clip(usable.severity.to_numpy(), *lgd.SEVERITY_CAP)
    info = {"resolved_defaults": int(len(usable)), "with_loss": int((sev > 0).sum()),
            "loss_per_dollar": float((sev * usable.default_upb).sum() / usable.default_upb.sum())}  # fmt: skip
    return model, info


def outcomes(con: Any, loans: Any, start: str, months: int, raw: bool = False) -> Any:
    """Per loan, the first exit after `start` within `months` months: event (defaulted / prepaid / censored /
    none), its month index (1 = the month after `start`) and the months whose outcome is known (all `months`
    for a default, payoff or survivor; up to censoring otherwise). `raw=True`: a default is also the first
    month 90+ days late by the reported (not stopped-clock) count, the forbearance flag ignored."""
    con.register("bk", loans[["loan_id"]])
    last = multistate.add_months(start, months)
    late = " or (p.reported_months_late >= 3 and p.status = 'delinquent')" if raw else ""
    df = con.sql(f"""
        with rows as (
          select p.loan_id, p.period_ym,
                 case when p.status = 'defaulted'{late} then 'defaulted' when p.status in ('prepaid', 'censored')
                      then p.status end as event
          from {PANEL} p join bk using (loan_id)
          where p.period_ym > '{start}' and p.period_ym <= '{last}'),
        first_exit as (
          select loan_id, arg_min(event, period_ym) as event, min(period_ym) as exit_ym
          from rows where event is not null group by loan_id)
        select bk.loan_id, coalesce(f.event, 'none') as event, f.exit_ym from bk left join first_exit f using (loan_id)
    """).df()  # fmt: skip
    con.unregister("bk")
    df["k"] = [multistate.months_between(start, e) if isinstance(e, str) else 0 for e in df["exit_ym"]]
    df["known"] = np.where(df["event"] == "censored", df["k"] - 1, months)
    return loans[["loan_id"]].merge(df, on="loan_id", how="left")


def actual_losses(defaults: Any, events: Any, booked_lgd: Any, any_time: bool = False) -> dict[str, Any]:
    """Freddie's reported net loss on each default (a gain counts as zero); a default not yet resolved is
    valued at the booked LGD. `defaults`: loan_id, exit_ym. `any_time`: match the loan's loss event whenever
    it came (the forbearance test's flag-ignored definition, whose defaults precede the loss event)."""
    e = events.copy()
    if any_time:
        e = e.sort_values("default_ym").groupby("loan_id", as_index=False).first()
        m = defaults[["loan_id"]].merge(e, on="loan_id", how="left")
    else:
        m = defaults[["loan_id", "exit_ym"]].merge(
            e, left_on=["loan_id", "exit_ym"], right_on=["loan_id", "default_ym"], how="left"
        )
    resolved = m.severity.notna()
    loss = np.where(resolved, np.clip(m.severity.fillna(0.0), 0.0, None) * m.default_upb.fillna(0.0), 0.0)
    unresolved = (~resolved) & m.default_upb.notna()
    ok = unresolved & m[list(lgd.FEATURES)].notna().all(axis=1)
    est = np.zeros(len(m))
    if ok.any():
        est[ok.to_numpy()] = booked_lgd.predict(lgd.matrix(m[ok])) * m.loc[ok, "default_upb"].to_numpy()
    matched = int(m.default_upb.notna().sum())
    total = float(loss.sum() + est.sum())
    return {"defaults": int(len(defaults)), "matched_to_loss_events": matched, "resolved": int(resolved.sum()),
            "unresolved_valued_at_booked_lgd": int(unresolved.sum()), "actual_loss": total,
            "share_of_loss_from_unresolved": float(est.sum() / total) if total > 0 else 0.0,
            "by_loan": dict(zip(m.loan_id, loss + est, strict=True))}  # fmt: skip


def price(
    book: Any, macro: Any, as_of: str, months: int, lgd_model: Any, inp: dict[str, Any]
) -> dict[str, Any]:
    p_def = book[[f"pd_{k}" for k in range(1, months + 1)]].to_numpy(dtype=float)
    p_pre = book[[f"pp_{k}" for k in range(1, months + 1)]].to_numpy(dtype=float)
    growth = backtest.long_run_growth(inp["actual"], as_of)
    out = engine.price(book, p_def, p_pre, macro, as_of, lgd_model, inp["curtail"], growth, monthly=True)
    return out | {"p_def": p_def}


def score(
    book: Any, priced: dict[str, Any], oc: Any, act: dict[str, Any], as_of: str, months: int
) -> dict[str, Any]:
    known = oc["known"].to_numpy()
    proj_def = backtest.counted_to_censoring(priced["p_def"], known)
    proj_el = backtest.counted_to_censoring(priced["el_monthly"], known)
    mlist = [multistate.add_months(as_of, k) for k in range(1, months + 1)]
    k = np.arange(months)[None, :]
    seen = k < known[:, None]
    actual_def = (oc["event"] == "defaulted").to_numpy()
    by_year_proj_def = backtest.yearly((priced["p_def"] * seen).sum(axis=0), mlist)
    by_year_proj_el = backtest.yearly((priced["el_monthly"] * seen).sum(axis=0), mlist)
    by_year_act_def = backtest.yearly(
        np.array([(oc["exit_ym"] == m).to_numpy()[actual_def].sum() for m in mlist]), mlist
    )
    loss_by_loan = oc["loan_id"].map(act["by_loan"]).fillna(0.0).to_numpy()
    by_year_act_loss = backtest.yearly(
        np.array([loss_by_loan[(oc["exit_ym"] == m).to_numpy() & actual_def].sum() for m in mlist]), mlist
    )
    bal = book["upb_start"].to_numpy(dtype=float)
    return {
        "as_of": as_of, "months": months, "loans": int(len(book)), "balance": float(bal.sum()),
        "projected_defaults": float(proj_def.sum()), "actual_defaults": int(actual_def.sum()),
        "projected_loss": float(proj_el.sum()), "actual_loss": act["actual_loss"],
        "defaults_ratio": float(proj_def.sum() / max(actual_def.sum(), 1)),
        "loss_ratio": float(proj_el.sum() / act["actual_loss"]) if act["actual_loss"] > 0 else None,
        "lifetime_reserve": float(priced["total"].sum()),
        "share_of_actual_loss_from_unresolved": act["share_of_loss_from_unresolved"],
        "actual_defaults_matched_to_loss_events": act["matched_to_loss_events"],
        "by_year": {y: {"projected_defaults": by_year_proj_def[y], "actual_defaults": by_year_act_def[y],
                        "projected_loss": by_year_proj_el[y], "actual_loss": by_year_act_loss[y]} for y in by_year_proj_def},
    }  # fmt: skip


def groups_table(book: Any, priced: dict[str, Any], oc: Any, act: dict[str, Any], key: str) -> dict[str, Any]:
    known = oc["known"].to_numpy()
    pd_ = backtest.counted_to_censoring(priced["p_def"], known)
    el = backtest.counted_to_censoring(priced["el_monthly"], known)
    is_def = (oc["event"] == "defaulted").to_numpy()
    loss = oc["loan_id"].map(act["by_loan"]).fillna(0.0).to_numpy() * is_def
    g = book[key].astype(str).to_numpy()
    return {str(v): {"projected_defaults": float(pd_[g == v].sum()), "actual_defaults": int(is_def[g == v].sum()),
                     "projected_loss": float(el[g == v].sum()), "actual_loss": float(loss[g == v].sum())}
            for v in sorted(set(g))}  # fmt: skip


def run_crisis(con: Any, inp: dict[str, Any]) -> dict[str, Any]:
    R, M = "200612", 48
    book = fetch_book(f"crisis_{R}")
    model, info = refit_lgd(inp["events"], R)
    priced = price(book, inp["actual"], R, M, model, inp)
    oc = outcomes(con, book, R, M)
    defaults = oc[oc.event == "defaulted"][["loan_id", "exit_ym"]]
    act = actual_losses(defaults, inp["events"], inp["booked_lgd"])
    res = score(book, priced, oc, act, R, M) | {"lgd_refit": info}
    res["by_vintage"] = groups_table(book, priced, oc, act, "vintage_year")
    res["by_starting_state"] = groups_table(book, priced, oc, act, "first_state")
    # benchmark: monthly default and payoff rates by loan age, 1999-2006, applied to the same book
    h = con.sql(f"""select loan_age, avg(case when status = 'defaulted' then 1.0 else 0 end) as h_d,
                           avg(case when status = 'prepaid' then 1.0 else 0 end) as h_p
                    from {PANEL} where period_ym between '199901' and '{R}' group by loan_age""").df().set_index("loan_age")  # fmt: skip
    age0 = book["loan_age"].to_numpy(dtype=int)
    surv = np.ones(len(book))
    b_def = np.zeros((len(book), M))
    for k in range(1, M + 1):
        hd = h["h_d"].reindex(age0 + k).fillna(h["h_d"].iloc[-60:].mean()).to_numpy()
        hp = h["h_p"].reindex(age0 + k).fillna(h["h_p"].iloc[-60:].mean()).to_numpy()
        b_def[:, k - 1] = surv * hd
        surv = surv * (1 - hd - hp)
    args = (book["upb_start"], book["rate_start"], book["remaining_start"], book["loan_age"])
    b_bal = np.column_stack([ead.project(*args, k, inp["curtail"]) for k in range(1, M + 1)])
    known = oc["known"].to_numpy()
    b_d = float(backtest.counted_to_censoring(b_def, known).sum())
    b_l = float(backtest.counted_to_censoring(b_def * b_bal * info["loss_per_dollar"], known).sum())
    res["benchmark"] = {"projected_defaults": b_d, "projected_loss": b_l,
                        "loss_per_dollar_pre2007": info["loss_per_dollar"]}  # fmt: skip
    res["verdict"] = backtest.crisis_verdict((res["projected_defaults"], res["actual_defaults"]),
                                             (res["projected_loss"], res["actual_loss"]),
                                             (b_d, res["actual_defaults"]), (b_l, res["actual_loss"]))  # fmt: skip
    return res


def run_rolling(con: Any, inp: dict[str, Any], dates: tuple[str, ...]) -> dict[str, Any]:
    M = 36
    res: dict[str, Any] = {"dates": {}}
    for R in dates:
        model, info = refit_lgd(inp["events"], R)
        row: dict[str, Any] = {"lgd_refit": info}
        for econ in ("actual", "flat"):
            book = fetch_book(f"rolling_{R}_{econ}")
            macro = inp["actual"] if econ == "actual" else backtest.flat_macro(inp["actual"], R, M + 3)
            priced = price(book, macro, R, M, model, inp)
            oc = outcomes(con, book, R, M)
            act = actual_losses(
                oc[oc.event == "defaulted"][["loan_id", "exit_ym"]], inp["events"], inp["booked_lgd"]
            )
            row[econ] = score(book, priced, oc, act, R, M)
            print(R, econ, round(row[econ]["loss_ratio"] or 0, 3), flush=True)
        res["dates"][R] = row
    for econ in ("actual", "flat"):
        per = {R: (r[econ]["projected_loss"], r[econ]["actual_loss"]) for R, r in res["dates"].items()}
        res[f"verdict_{econ}"] = backtest.rolling_verdict(per)
    res["verdict"] = res["verdict_actual"]  # judged on economy (a); (b) is information
    return res


def run_forbearance(con: Any, inp: dict[str, Any]) -> dict[str, Any]:
    R, M = "202006", 36
    # counts, January 2020 - December 2021: (A) booked, (B) forbearance flag ignored
    counts = con.sql(f"""
        with b as (select loan_id, period_ym, status, reported_months_late, effective_months_late, flagged_pause
                   from {PANEL} where period_ym between '201912' and '202312'),
        a_def as (select loan_id, min(period_ym) as ym from b where status = 'defaulted' group by loan_id),
        b_def as (select loan_id, min(period_ym) as ym from b
                  where status = 'defaulted' or (status = 'delinquent' and reported_months_late >= 3) group by loan_id)
        select b_def.loan_id, b_def.ym as b_ym, a_def.ym as a_ym from b_def left join a_def using (loan_id)
        where b_def.ym between '202001' and '202112'
    """).df()  # fmt: skip
    a_only = con.sql(f"select substr(period_ym, 1, 4) as y, count(*) as n from {PANEL} where status = 'defaulted' "
                     "and period_ym between '202001' and '202112' group by 1").df().set_index("y")["n"].to_dict()  # fmt: skip
    extra = counts[counts.a_ym.isna() | (counts.a_ym > counts.b_ym)]
    con.register("ex", extra[["loan_id", "b_ym"]])
    cured = con.sql(f"""select count(distinct p.loan_id) from {PANEL} p join ex using (loan_id)
                        where p.period_ym > ex.b_ym and p.period_ym <= strftime(strptime(ex.b_ym, '%Y%m') + interval 12 month, '%Y%m')
                          and p.status = 'current' and coalesce(p.reported_months_late, 0) = 0""").fetchall()[0][0]  # fmt: skip
    con.unregister("ex")
    ev = inp["events"]
    later_loss = int(extra.merge(ev[ev.severity > 0][["loan_id"]].drop_duplicates(), on="loan_id").shape[0])
    res: dict[str, Any] = {"counts_2020_2021": {
        "defaults_booked_A_by_year": {str(k): int(v) for k, v in a_only.items()},
        "defaults_flag_ignored_B": int(len(counts)), "extra_under_B_only": int(len(extra)),
        "extra_current_again_within_12m": int(cured), "extra_with_a_later_loss": later_loss}}  # fmt: skip
    # B: loans already in default by the reported count before July 2020 are out of B's book
    pre = set(counts[counts.b_ym <= R].loan_id) - set(
        counts[counts.a_ym.notna() & (counts.a_ym <= R)].loan_id
    )
    for label, tag, raw in (("A_booked", "A", False), ("B_flag_ignored", "B", True)):
        book = fetch_book(f"forbearance_{R}_{tag}")
        if raw:
            book = book[~book.loan_id.isin(pre)].reset_index(drop=True)
        priced = price(book, inp["actual"], R, M, inp["booked_lgd"], inp)
        oc = outcomes(con, book, R, M, raw=raw)
        act = actual_losses(
            oc[oc.event == "defaulted"][["loan_id", "exit_ym"]], ev, inp["booked_lgd"], any_time=raw
        )
        res[label] = score(book, priced, oc, act, R, M)
    res["B_book_excludes_defaulted_by_reported_count_before_july_2020"] = len(pre)
    c = res["counts_2020_2021"]
    res["verdict"] = backtest.forbearance_verdict(
        (res["A_booked"]["projected_loss"], res["A_booked"]["actual_loss"]),
        (res["B_flag_ignored"]["projected_loss"], res["B_flag_ignored"]["actual_loss"]),
        c["extra_under_B_only"], c["extra_current_again_within_12m"])  # fmt: skip
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--test", required=True, choices=("crisis", "rolling", "forbearance"))
    parser.add_argument("--dates", nargs="*", default=list(ROLLING_DATES))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    environment.current_env()
    import duckdb

    con = duckdb.connect()
    con.execute("set memory_limit='3GB'")
    inp = inputs()
    if args.test == "crisis":
        result = run_crisis(con, inp)
    elif args.test == "rolling":
        result = run_rolling(con, inp, tuple(args.dates))
    else:
        result = run_forbearance(con, inp)
    print(json.dumps(result.get("verdict"), indent=1, default=float))
    if args.write:
        out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"note": NOTE}
        out[args.test] = result
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
