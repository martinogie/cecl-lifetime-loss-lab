"""Phase 5: price the stress and sensitivity runs and build every Phase 5 output (aggregates only).

    CECL_ENV=local python -m scripts.build_stress [--write]

Inputs (git-ignored, loan-level): the 30 June 2026 projected books, data/local/reserve/book_<tag>.parquet,
from the Databricks job `reserve_projection` (Phase 4 for the booked runs; `variants=` for Phase 5). A book
not on the PC is downloaded from the volume, priced, and its local copy removed again (the volume keeps
it), so the PC never holds more than one extra book (each is about 225 MB). Each priced run is kept as a
small aggregate file (data/local/reserve/stress/agg_<tag>.json), so a re-run only prices what is new.

What it builds (decisions: Martins, 20 November 2026; `docs/phase5/plan.md`):
1. The Fed baseline, adverse and severely adverse paths quarter by quarter: expected defaults, losses,
   loss rates, the cumulative 9-quarter loss rate and the lifetime reserve.
2. Custom shocks on the Fed baseline (`cecl.scenarios.SHOCKS`), including the immediate-jump case.
3. The sensitivity table: forecast period, reversion speed, Fed timing, payoff speed (new projections),
   LGD floor (pricing only), scenario weights and the L-038 uplift, ranked by how much each moves the
   reserve.
4. The benchmark: the 9-quarter severely adverse loss rate vs the Fed's DFAST 2026 first-lien mortgage loss
   rate (32 banks, 1.5% aggregate, 0.9-2.8%), with Freddie Mac's 0.19% allowance ratio as context.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from cecl import engine, environment, lgd, scenarios, stress
from scripts import build_reserve as br

R = "202606"
RES = br.RES
CACHE = RES / "stress"
OUT = Path("docs/phase5/stress_2026Q2.json")
RESERVE_FILE = Path("docs/phase4/reserve_2026Q2.json")
VOLUME = "dbfs:/Volumes/cecl_lab_dev/bronze/raw/results/reserve"
PROFILE = "cecl"
DFAST = {  # Fed, 2026 stress test results (24 June 2026), public_results_DFAST_2026.csv
    "source": "Federal Reserve, 2026 Supervisory Stress Test results (24 June 2026), public_results_DFAST_2026.csv: "
    "domestic first-lien mortgage loss rate, severely adverse, 9 quarters (2026 Q1 - 2028 Q1), losses as a % of "
    "average balances",
    "aggregate_32_banks": 0.015,
    "range": [0.009, 0.028],
    "examples": {"Wells Fargo": 0.009, "JPMorgan Chase": 0.012, "Bank of America": 0.013, "Citigroup": 0.020},
}
FREDDIE_RATIO = 0.0019  # Freddie Mac Q2 2026 earnings release (context only)
NINE = 9  # DFAST horizon in quarters


def runs() -> list[tuple[str, str, float]]:
    """(variant, scenario, LGD floor) for every run: booked, LGD floors on the booked books, variants."""
    out = [("", s, 0.0) for s in scenarios.SCENARIOS]
    out += [("", s, f) for f in stress.LGD_FLOORS for s in scenarios.SCENARIOS]
    out += [(v, s, 0.0) for v, spec in stress.VARIANTS.items() for s in spec.scenarios]
    return out


def cache_name(variant: str, scenario: str, floor: float) -> Path:
    t = stress.tag(R, R, scenario, variant)
    return CACHE / (f"agg_{t}" + (f"__floor{int(round(floor * 100)):02d}" if floor else "") + ".json")


def fetch(tag: str) -> tuple[Path, bool]:
    """The book file on the PC; downloaded from the volume if missing (then True: remove after pricing)."""
    path = RES / f"book_{tag}.parquet"
    if path.exists():
        return path, False
    subprocess.run(["databricks", "fs", "cp", f"{VOLUME}/book_{tag}.parquet", str(path), "--profile", PROFILE,
                    "--overwrite"], check=True, capture_output=True)  # fmt: skip
    return path, True


def scenario_for(variant: str, scenario: str, inp: dict[str, Any], weights: dict[str, float]) -> Any:
    """The same economy the projection job used for this run."""
    import pandas as pd

    spec = stress.VARIANTS.get(variant)
    if spec and spec.timing == "calendar":
        fed = pd.read_parquet(br.LOCAL / "silver" / "macro" / f"{stress.FED_TABLE_CALENDAR}.parquet")
    else:
        fed = inp["fed"][scenarios.fed_year(R)]
    return scenarios.build(
        scenario, R, inp["state"], inp["national"], fed, inp["long_run"], weights, horizon=engine.MONTHLY + 3,
        forecast_months=spec.forecast_months if spec else scenarios.FORECAST_MONTHS,
        reversion_months=spec.reversion_months if spec else scenarios.REVERSION_MONTHS,
        shock=scenarios.SHOCKS[spec.shock] if spec and spec.shock else None,
    )  # fmt: skip


def national_path(sc: Any, weights: dict[str, float]) -> dict[str, list[float]]:
    """Book-balance-weighted state paths at each quarter-end after R: unemployment and the house-price change
    (log ratio to R, shown as a % change), plus the national mortgage rate."""
    states = [s for s in sc.macro.hpi.columns if s in weights]
    w = np.array([weights[s] for s in states])
    w = w / w.sum()
    ends = [sc.months[3 * q - 1] for q in range(1, engine.MONTHLY // 3 + 1)]
    h0 = sc.macro.hpi.loc[sc.reserve_ym, states].to_numpy(dtype=float)
    return {
        "quarter_end": ends,
        "unemployment": [float(sc.macro.unemployment.loc[m, states].to_numpy(dtype=float) @ w) for m in ends],
        "hpi_change": [float(np.expm1(np.log(sc.macro.hpi.loc[m, states].to_numpy(dtype=float) / h0) @ w)) for m in ends],
        "mortgage_rate": [float(sc.macro.mortgage_rate[m]) for m in ends],
    }  # fmt: skip


def price_one(variant: str, scenario: str, floor: float, inp: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    cache = cache_name(variant, scenario, floor)
    if cache.exists():
        return dict(json.loads(cache.read_text(encoding="utf-8")))
    tag = stress.tag(R, R, scenario, variant)
    path, downloaded = fetch(tag)
    try:
        book = pd.read_parquet(path)
        spec = stress.VARIANTS.get(variant)
        want = (spec.forecast_months, spec.reversion_months) if spec else (24, 12)
        got = tuple(int(book[c].iloc[0]) if c in book.columns else d
                    for c, d in (("forecast_months", 24), ("reversion_months", 12)))  # fmt: skip
        if got != want:
            raise SystemExit(f"{tag}: projected with forecast/reversion {got}, expected {want}")
        p_def = book[[f"pd_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
        p_pre = book[[f"pp_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
        weights = book.groupby("property_state")["upb_start"].sum().to_dict()
        sc = scenario_for(variant, scenario, inp, weights)
        model = lgd.FlooredLGD(inp["lgd"], floor) if floor else inp["lgd"]
        fm = spec.forecast_months if spec else scenarios.FORECAST_MONTHS
        out = engine.price(book, p_def, p_pre, sc.macro, R, model, inp["curtail"], sc.long_run["hpi_growth"], fm,
                           quarterly=True)  # fmt: skip
        bal = book["upb_start"].to_numpy(dtype=float)
        agg = {
            "variant": variant, "scenario": scenario, "lgd_floor": floor, "loans": int(len(book)),
            "balance": float(bal.sum()), "reserve": float(out["total"].sum()),
            "forecast": float(out["forecast"].sum()), "reversion": float(out["reversion"].sum()),
            "tail": float(out["tail"].sum()),
            "lifetime_pd": float((out["lifetime_pd"] * bal).sum() / bal.sum()),
            "expected_payoffs_36": float(p_pre.sum()),
            "q_el": out["q_el"].sum(axis=0).tolist(), "q_defaults": out["q_pd"].sum(axis=0).tolist(),
            "q_bal": out["q_bal"].sum(axis=0).tolist(),
            # tie-out: the book reserve equals the sum of its parts (forecast + reversion + tail), loan by loan summed
            "tie_out": bool(abs(float(out["total"].sum()) - float((out["forecast"] + out["reversion"] + out["tail"]).sum())) < 0.01),
            "paths": national_path(sc, weights),
        }  # fmt: skip
    finally:
        if downloaded:
            path.unlink()  # the volume keeps the book; the PC keeps only the aggregates
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(agg), encoding="utf-8")
    return agg


def quarterly(a: dict[str, Any]) -> dict[str, Any]:
    """Quarter-by-quarter table and the 9-quarter loss rates (on the starting balance and, like DFAST, on the
    average balance over the 9 quarters)."""
    bal0 = a["balance"]
    q_avg = [b / 3.0 for b in a["q_bal"]]
    avg9 = (bal0 + sum(q_avg[:NINE]) * 1.0) / (NINE + 1)  # start and the 9 quarterly averages
    rows = [{"quarter": i + 1, "quarter_end": a["paths"]["quarter_end"][i], "expected_defaults": a["q_defaults"][i],
             "expected_loss": a["q_el"][i], "loss_rate_of_start_balance": a["q_el"][i] / bal0,
             "average_balance": q_avg[i], "unemployment": a["paths"]["unemployment"][i],
             "hpi_change": a["paths"]["hpi_change"][i], "mortgage_rate": a["paths"]["mortgage_rate"][i]}
            for i in range(len(a["q_el"]))]  # fmt: skip
    loss9 = sum(a["q_el"][:NINE])
    return {"quarters": rows, "loss_9q": loss9, "loss_rate_9q_of_start_balance": loss9 / bal0,
            "loss_rate_9q_of_average_balance": loss9 / avg9, "average_balance_9q": avg9,
            "lifetime_reserve": a["reserve"], "lifetime_rate": a["reserve"] / bal0, "lifetime_pd": a["lifetime_pd"]}  # fmt: skip


def weighted(by_s: dict[str, dict[str, Any]], w: tuple[float, float, float] = (0.6, 0.3, 0.1)) -> float:
    return float(sum(wi * by_s[s]["reserve"] for wi, s in zip(w, scenarios.SCENARIOS, strict=True)))


def build(aggs: dict[tuple[str, str, float], dict[str, Any]]) -> dict[str, Any]:
    booked = {s: aggs[("", s, 0.0)] for s in scenarios.SCENARIOS}
    base_w = weighted(booked)
    reserve_file = json.loads(RESERVE_FILE.read_text(encoding="utf-8"))["dates"][R]
    pipeline = reserve_file["defaulted_pipeline"]["reserve"]
    if abs(base_w - reserve_file["weighted_reserve"]) > 1.0:  # the booked runs must reproduce Phase 4
        raise SystemExit(
            f"booked weighted reserve {base_w:,.2f} differs from Phase 4 {reserve_file['weighted_reserve']:,.2f}"
        )
    res: dict[str, Any] = {
        "note": "Aggregates only. Educational model on Freddie Mac conforming mortgages (sample book); not an audited "
        "bank reserve and not accounting advice. Model v2 @challenger failed its holdout (L-038); the holdout check "
        "was biased (L-039); no L-038 overlay (Option B).",
        "reserve_date": "2026-06-30", "book": {"loans": booked["baseline"]["loans"], "balance": booked["baseline"]["balance"]},
        "booked": {"weighted_reserve": base_w, "defaulted_reserve": pipeline, "total_allowance": base_w + pipeline},
        "fed_quarterly": {s: quarterly(a) for s, a in booked.items()},
    }  # fmt: skip
    base = booked["baseline"]
    res["shocks"] = {}
    for k in scenarios.SHOCKS:
        a = aggs[(k, "baseline", 0.0)]
        q = quarterly(a)
        res["shocks"][k] = {"label": stress.VARIANTS[k].label, "reserve": a["reserve"],
                            "change_vs_baseline": a["reserve"] - base["reserve"],
                            "multiple_of_baseline": a["reserve"] / base["reserve"],
                            "loss_rate_9q_of_average_balance": q["loss_rate_9q_of_average_balance"],
                            "lifetime_pd": a["lifetime_pd"], "worst_hpi_change": min(a["paths"]["hpi_change"]),
                            "peak_unemployment": max(a["paths"]["unemployment"]),
                            "information_only": k == "hpi_m30_immediate"}  # fmt: skip
    sens: list[dict[str, Any]] = []

    def add(group: str, label: str, value: float, booked_flag: bool = False) -> None:
        sens.append({"assumption": group, "setting": label, "weighted_reserve": value,
                     "change": value - base_w, "change_pct": value / base_w - 1, "booked": booked_flag,
                     "total_allowance": value + pipeline})  # fmt: skip

    for group, keys, booked_label in (
        ("Forecast period", (("forecast_12", "12 months"), ("forecast_36", "36 months")), "24 months"),
        ("Reversion speed", (("reversion_6", "6 months"), ("reversion_24", "24 months")), "12 months"),
        ("Fed timing", (("timing_calendar", "calendar quarters"),), "shifted"),
        ("Payoff speed", (("payoff_08", "x0.8"), ("payoff_12", "x1.2")), "x1.0 (as fitted)"),
    ):
        add(group, booked_label, base_w, True)
        for v, label in keys:
            add(group, label, weighted({s: aggs[(v, s, 0.0)] for s in scenarios.SCENARIOS}))
    add("LGD floor (severity of loans that lose)", "none", base_w, True)
    for f in stress.LGD_FLOORS:
        add(
            "LGD floor (severity of loans that lose)",
            f"{f:.0%}",
            weighted({s: aggs[("", s, f)] for s in scenarios.SCENARIOS}),
        )
    for name, w in stress.WEIGHT_SETS.items():
        add("Scenario weights (baseline/adverse/severe)", name, weighted(booked, w), "booked" in name)
    add("Default uplift (L-038 Option A)", "0% (Option B)", base_w, True)
    add("Default uplift (L-038 Option A)", "+34%", base_w * (1 + br.UPLIFT))
    res["sensitivity"] = sens
    ranges: dict[str, tuple[float, float]] = {}
    for row in sens:
        lo, hi = ranges.get(row["assumption"], (0.0, 0.0))
        ranges[row["assumption"]] = (min(lo, row["change"]), max(hi, row["change"]))
    res["tornado"] = sorted(({"assumption": k, "low_change": lo, "high_change": hi, "range": hi - lo}
                             for k, (lo, hi) in ranges.items()), key=lambda r: -r["range"])  # fmt: skip
    sev = res["fed_quarterly"]["severely_adverse"]
    res["benchmark"] = {
        "ours_severely_adverse_9q_loss_rate_of_average_balance": sev["loss_rate_9q_of_average_balance"],
        "ours_severely_adverse_9q_loss_rate_of_start_balance": sev["loss_rate_9q_of_start_balance"],
        "ours_window": f"{sev['quarters'][0]['quarter_end']} - {sev['quarters'][NINE - 1]['quarter_end']} (quarter-end months)",
        "dfast_2026": DFAST,
        "freddie_allowance_ratio_context": FREDDIE_RATIO,
        "our_total_allowance_ratio": reserve_file["total_allowance_ratio"],
    }  # fmt: skip
    res["tie_outs"] = {
        "booked_reproduces_phase4": True,
        "every_run_reserve_equals_its_parts": all(a["tie_out"] for a in aggs.values()),
    }
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--booked-only", action="store_true", help="price only the runs on the Phase 4 books, then stop"
    )
    args = parser.parse_args(argv)
    environment.current_env()
    import duckdb

    stress.check()
    inp = br.load_inputs(duckdb.connect())
    aggs = {}
    todo = [r for r in runs() if not args.booked_only or r[0] == ""]
    for i, (v, s, f) in enumerate(todo, 1):
        aggs[(v, s, f)] = price_one(v, s, f, inp)
        print(
            f"[{i}/{len(todo)}] {v or 'booked'} {s} floor={f}: {aggs[(v, s, f)]['reserve']:,.0f}", flush=True
        )
    if args.booked_only:
        return 0
    res = build(aggs)
    print(
        json.dumps(
            {"booked": res["booked"], "tornado": res["tornado"], "benchmark": res["benchmark"]},
            indent=1,
            default=float,
        )
    )
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
