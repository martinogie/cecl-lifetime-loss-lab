"""Phase 4: price the reserve, weight the scenarios, and build every reserve output (aggregates only).

    CECL_ENV=local python -m scripts.build_reserve [--write]

The calculations live in `cecl/close.py` (Phase 8: the same code closes any quarter, here or in the prod
close job); this script is the PC wrapper for the three dated reserves of Phase 4, over the local files.

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
6. Disclosure tables: rollforward by loan group; credit quality (FICO and LTV bands) by origination year;
   gross write-offs by origination year.
7. The overlay register for each date (ADR-006, `cecl.overlays`, log `docs/governance/overlay_log.json`):
   only approved overlays that pass every rule are booked, on top of the modelled reserve.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any

from cecl import close, environment, overlays, scenarios

LOCAL = Path("data/local")
RES = LOCAL / "reserve"
OUT = Path("docs/phase4/reserve_2026Q2.json")
Q0, Q1, Q2 = "202512", "202603", "202606"
QUARTERS = ((Q0, Q1, "q1_2026", "202601", "202603"), (Q1, Q2, "q2_2026", "202604", "202606"))
OVERLAY_LOG = Path("docs/governance/overlay_log.json")
WEIGHTS = close.WEIGHTS
UPLIFT = close.UPLIFT
FORECAST_MONTHS = close.FORECAST_MONTHS
REVERSION_MONTHS = close.REVERSION_MONTHS
group_of = close.group_of
REL = {
    "perf": f"read_parquet('{LOCAL}/bronze/freddie_perf/**/*.parquet', hive_partitioning=true)",
    "orig": f"read_parquet('{LOCAL}/bronze/freddie_orig/**/*.parquet', hive_partitioning=true)",
    "events": f"read_parquet('{LOCAL}/phase3/lgd_events.parquet')",
}
PANEL = f"read_parquet('{LOCAL}/panel/**/*.parquet', hive_partitioning=true)"


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
    """Each loan's lifetime expected loss under one scenario, the economy dated E, for the book at R (cached
    next to the projection file it came from)."""
    import pandas as pd

    src, cache = RES / f"book_{R}_{E}_{name}.parquet", RES / f"priced_{R}_{E}_{name}.parquet"
    if cache.exists() and cache.stat().st_mtime > src.stat().st_mtime:
        return pd.read_parquet(cache)  # already priced from this exact projection file
    book = pd.read_parquet(src)
    close.check_settings(book, src)
    e_book = (
        book
        if E == R
        else pd.read_parquet(RES / f"book_{E}_{E}_{name}.parquet", columns=["property_state", "upb_start"])
    )
    frame = close.price(book, R, E, name, inp, close.state_weights(e_book))
    frame.to_parquet(cache, index=False)
    return frame


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
        frames = {s: runs[(R, R, s)] for s in scenarios.SCENARIOS}
        modelled = close.weighted({s: float(f["total"].sum()) for s, f in frames.items()})
        pipelines[R] = close.defaulted_pipeline(con, REL, R, inp["lgd"])
        # ADR-006: the register checks every overlay against the modelled (weighted) reserve; only booked ones count
        res["dates"][R] = close.date_block(
            frames, pipelines[R], R, overlays.register(log, modelled, R), assessed
        )
    orig_year = con.sql("select loan_id, cast(substr(first_payment_date, 1, 4) as int) as vintage_year from "
                        f"{REL['orig']}").df()  # fmt: skip

    def booked(R: str) -> Any:
        frames = {s: runs[(R, R, s)] for s in scenarios.SCENARIOS}
        return close.booked_frame(
            frames, pipelines[R], orig_year, res["dates"][R]["overlays"]["booked_total"]
        )

    wo_q2 = None
    for a, b_, tag, first_m, last_m in QUARTERS:
        start, end = booked(a), booked(b_)
        wo = close.losses(con, REL, first_m, last_m, inp["lgd"])
        wo = wo.merge(orig_year, on="loan_id", how="left")  # every loss realised in the quarter
        wo["group"] = group_of(wo["vintage_year"].fillna(2000))
        rf = close.rollforward(start, end, wo)
        rf["write_off_events"] = int(len(wo))
        rf["estimated_write_off_events"] = int(wo["estimated"].sum())
        rf["note"] = (
            "Freddie reports losses about 3 months after the sale; write-offs not yet reported are estimated "
            "(balance removed x Phase 3 LGD) and trued up when reported"
        )
        res[f"rollforward_{tag}"] = rf
        br = close.finish_bridge(close.bridge(runs, a, b_), res["dates"][a], res["dates"][b_])
        res["bridge_q4_2025_to_q1_2026" if tag == "q1_2026" else "bridge_q1_to_q2_2026"] = br
        if tag == "q2_2026":
            wo_q2 = wo
    year_before = f"{int(Q2[:4]) - 1}12"
    lev = close.losses(con, REL, "199901", year_before)
    bal = con.sql(f"select substr(period_ym, 1, 4) as year, sum(current_upb) / 12.0 as avg_balance "
                  f"from {PANEL} where period_ym <= '{year_before}' group by 1").df().set_index("year")["avg_balance"]  # fmt: skip
    ob = con.sql(f"select cast(substr(first_payment_date, 1, 4) as int) as vintage_year, sum(orig_upb) as orig_balance "
                 f"from {REL['orig']} group by 1").df().set_index("vintage_year")["orig_balance"]  # fmt: skip
    base = pd.read_parquet(RES / f"book_{Q2}_{Q2}_baseline.parquet")
    res["benchmarks"] = close.benchmarks(lev, bal, orig_year, ob, base, Q2)
    wo_ytd = close.losses(con, REL, "202601", "202606", inp["lgd"])
    assert wo_q2 is not None
    res["disclosures"] = close.disclosures(
        runs[(Q2, Q2, "baseline")], wo_ytd, wo_q2.drop(columns=["vintage_year", "group"]), orig_year, Q2
    )
    res["disclosures"]["rollforward_by_loan_group"] = {
        "q1_2026": res["rollforward_q1_2026"]["by_group"],
        "q2_2026": res["rollforward_q2_2026"]["by_group"],
    }
    print(json.dumps({R: {k: res["dates"][R][k] for k in ("book", "weighted_reserve", "reserve_ratio")}
                      | {"scenarios": {s: round(v["reserve"]) for s, v in res["dates"][R]["scenarios"].items()},
                         "tie_outs": {s: t["passes"] for s, t in res["dates"][R]["tie_outs"].items()}}
                      for R in res["dates"]}, indent=1, default=float))  # fmt: skip
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
