"""Build the website's data files from the project's aggregate results (Phase 7).

    CECL_ENV=local python site/scripts/export_site_data.py   (writes site/public/data/, for the build and tests)

The same `build()` feeds `ingestion/site_publish.py`, which publishes the files to the private R2 bucket after
a successful prod run (the site then refreshes without a rebuild, as Bank Health's does).

Sources: the committed aggregates (docs/phase4/reserve_2026Q2.json, reserve_components_2026Q2.json,
docs/phase5/stress_2026Q2.json, docs/phase6/*.json when present) plus one new set of aggregates, the
reserve by loan group for the Loss explorer, computed here from the priced loan files on the PC
(data/local/reserve/priced_*.parquet, git-ignored). Only sums by group leave the PC: any group with fewer
than MIN_LOANS loans is folded into "Other", so no cell can point at a single loan.

Every file carries `dataAsOf` (the reserve date and the date written) and the model-status statement, which
the site shows on every page. The schemas are in site/data/schemas/ and CI checks every file against them.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from cecl.scheduling import TZ

ROOT = Path(__file__).resolve().parents[1]  # the repository (committed aggregates under docs/)
OUT = ROOT / "site" / "public" / "data"
DATA_ROOT = ROOT / "data"  # the data folder (git-ignored); /opt/airflow/data in the Airflow containers
# The Loss explorer's group totals (aggregates only), saved when the data is exported on the PC so the VM's
# publish step never needs the loan-level priced files (only code, logs and small outputs live on the VM).
BY_GROUP = "docs/phase4/reserve_by_group_2026Q2.json"
FILES = (
    "overview.json",
    "explorer.json",
    "scenarios.json",
    "disclosures.json",
    "methods.json",
    "pipeline.json",
)
MIN_LOANS = 50
WEIGHTS = {"baseline": 0.60, "adverse": 0.30, "severely_adverse": 0.10}
FREDDIE_RATIO = 0.0019
STATUS = {
    "model": "Model v2 (challenger)",
    "holdout": "v2 failed its holdout (July 2024 - June 2026, run once): 0.81 of actual defaults under the recorded check, 0.92 counted correctly (L-038, L-039).",
    "overlay": "No overlay (Option B): the LGD model's conservatism (1.64x the real loss per default, L-033) more than offsets the default shortfall (0.74 in loss terms). The +34% default uplift is shown as a sensitivity.",
    "framing": "Educational model on Freddie Mac conforming mortgages (sample book). Not an audited bank reserve and not accounting advice.",
}  # fmt: skip


def read(rel: str) -> dict[str, Any]:
    p = ROOT / rel
    return dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else {}


def m(x: float) -> float:
    """Dollars to $ millions, 3 decimals (the site formats them)."""
    return round(float(x) / 1e6, 3)


def written_on(now: datetime | None = None) -> str:
    """The date the files are written, in Chicago (the project's time zone). The VM's containers run on UTC,
    so date.today() there showed tomorrow's date after 18:00 Chicago (first prod publish, 22 Nov 2026)."""
    return (now or datetime.now(TZ)).astimezone(TZ).date().isoformat()


def header() -> dict[str, Any]:
    return {"dataAsOf": {"reserveDate": "2026-06-30", "updated": written_on()}, "status": STATUS}


def overview(
    r: dict[str, Any], comp: dict[str, Any], stress: dict[str, Any], peer: dict[str, Any]
) -> dict[str, Any]:
    dates = {}
    for k, label in (("202512", "31 Dec 2025"), ("202603", "31 Mar 2026"), ("202606", "30 Jun 2026")):
        d = r["dates"][k]
        dates[k] = {"label": label, "loans": d["book"]["loans"], "balanceM": m(d["book"]["balance"]),
                    "performingM": m(d["weighted_reserve"]), "defaultedM": m(d["defaulted_pipeline"]["reserve"]),
                    "totalM": m(d["total_allowance"]), "ratio": d["total_allowance_ratio"],
                    "totalWithUpliftM": m(d["total_allowance_with_uplift"]), "fedScenarioYear": d["fed_scenario_year"]}  # fmt: skip
    q2 = r["dates"]["202606"]
    scen = {s: {"reserveM": m(v["reserve"]), "lifetimePd": v["lifetime_pd_weighted_by_balance"],
                "withUpliftM": m(v["reserve_with_uplift"])} for s, v in q2["scenarios"].items()}  # fmt: skip
    pipe = q2["defaulted_pipeline"]
    out: dict[str, Any] = header() | {
        "dates": dates,
        "scenarios": scen,
        "weights": WEIGHTS,
        "components": {s: {"lifetimePd": c["reconciling_chain"]["lifetime_pd"], "eadRatio": c["reconciling_chain"]["ead_ratio"],
                           "lgd": c["reconciling_chain"]["lgd"], "reserveM": m(c["reserve"])}
                       for s, c in comp.get("scenarios", {}).items()},
        "benchmarks": {"lossRateM": m(r["benchmarks"]["loss_rate_method"]["reserve"]),
                       "vintageM": m(r["benchmarks"]["vintage_method"]["reserve"]),
                       "freddieRatio": FREDDIE_RATIO, "freddieOnOurBookM": m(FREDDIE_RATIO * q2["book"]["balance"])},
        "ratios": {"total": q2["total_allowance_ratio"],
                   "baselineOnly": (q2["scenarios"]["baseline"]["reserve"] + pipe["reserve"]) / (q2["book"]["balance"] + pipe["balance"]),
                   "lgdConservatismRemoved": q2["total_allowance_ratio"] / 1.64},
        "stress": {"severeNineQuarterLossRate": stress["benchmark"]["ours_severely_adverse_9q_loss_rate_of_average_balance"],
                   "dfastAggregate": stress["benchmark"]["dfast_2026"]["aggregate_32_banks"],
                   "dfastRange": stress["benchmark"]["dfast_2026"]["range"]},
    }  # fmt: skip
    agg = [a for a in peer.get("aggregates", []) if a["period"] == "2026-06-30"]
    out["peers"] = [{"group": a["size_group"], "banks": a["banks"], "p10": a["p10"], "p25": a["p25"], "median": a["median"],
                     "p75": a["p75"], "p90": a["p90"], "pooled": a["pooled"]} for a in agg]  # fmt: skip
    return out


def explorer(data_root: Path = DATA_ROOT) -> dict[str, Any]:
    """Reserve by loan group at 30 June 2026 (weighted and per scenario), from the priced loan files."""
    import pandas as pd

    reserve_dir = Path(data_root) / "local" / "reserve"
    frames = {s: pd.read_parquet(reserve_dir / f"priced_202606_202606_{s}.parquet",
                                 columns=["loan_id", "vintage_year", "property_state", "credit_score", "current_ltv",
                                          "upb_start", "total", "lifetime_pd"]) for s in WEIGHTS}  # fmt: skip
    base = frames["baseline"]
    for s, f in frames.items():
        if not np.array_equal(f["loan_id"].to_numpy(), base["loan_id"].to_numpy()):
            raise SystemExit(f"loan order differs in {s}")
    bal = base["upb_start"].to_numpy(dtype=float)
    res = {s: f["total"].to_numpy(dtype=float) for s, f in frames.items()}
    res["weighted"] = sum(WEIGHTS[s] * res[s] for s in WEIGHTS)
    pdw = sum(WEIGHTS[s] * frames[s]["lifetime_pd"].to_numpy(dtype=float) for s in WEIGHTS)
    score = base["credit_score"].to_numpy(dtype=float)
    ltv = base["current_ltv"].to_numpy(dtype=float)
    groups = {
        "vintage": base["vintage_year"].astype(int).astype(str).to_numpy(),
        "creditScore": np.select([score < 620, score < 680, score < 740], ["Under 620", "620-679", "680-739"], "740 and over"),
        "currentLtv": np.select([ltv <= 60, ltv <= 80, ltv <= 90], ["60% or less", "60-80%", "80-90%"], "Over 90%"),
        "state": base["property_state"].astype(str).to_numpy(),
    }  # fmt: skip
    out: dict[str, Any] = header() | {"minLoansPerGroup": MIN_LOANS, "dimensions": {}}
    total_w = res["weighted"].sum()
    for dim, g in groups.items():
        keys = pd.Series(g)
        counts = keys.value_counts()
        small = set(counts[counts < MIN_LOANS].index)
        if dim == "state":  # top 15 states by weighted reserve, the rest together
            by = pd.Series(res["weighted"]).groupby(keys).sum().sort_values(ascending=False)
            small |= set(by.index[15:])
        g2 = np.where(keys.isin(small), "Other", g)
        rows: list[dict[str, Any]] = []
        for v in sorted(set(g2), key=lambda x: (x == "Other", x)):
            mask = g2 == v
            rows.append({"group": str(v), "loans": int(mask.sum()), "balanceM": m(bal[mask].sum()),
                         "reserveM": {s: m(res[s][mask].sum()) for s in ("weighted", *WEIGHTS)},
                         "ratio": float(res["weighted"][mask].sum() / bal[mask].sum()),
                         "lifetimePd": float((pdw[mask] * bal[mask]).sum() / bal[mask].sum()),
                         "shareOfReserve": float(res["weighted"][mask].sum() / total_w)})  # fmt: skip
        if dim == "state":
            rows.sort(key=lambda x: (x["group"] == "Other", -x["reserveM"]["weighted"]))
        out["dimensions"][dim] = rows
    out["totals"] = {
        "loans": int(len(bal)),
        "balanceM": m(bal.sum()),
        "reserveM": {s: m(v.sum()) for s, v in res.items()},
    }
    return out


def scenarios(stress: dict[str, Any]) -> dict[str, Any]:
    names = {"hpi_m10": "House prices -10%", "hpi_m20": "House prices -20%", "hpi_m30": "House prices -30%",
             "unemployment_p2": "Unemployment +2 points", "unemployment_p4": "Unemployment +4 points",
             "rates_up2": "Mortgage rate +2 points", "rates_down2": "Mortgage rate -2 points",
             "hpi_m20_unemployment_p2": "House prices -20% and unemployment +2",
             "hpi_m30_unemployment_p4": "House prices -30% and unemployment +4",
             "hpi_m30_immediate": "House prices -30% at once (information only)"}  # fmt: skip
    fed = {s: {"loss9qM": m(q["loss_9q"]), "lossRate9q": q["loss_rate_9q_of_average_balance"], "lifetimeM": m(q["lifetime_reserve"]),
               "lifetimePd": q["lifetime_pd"],
               "quarters": [{"quarterEnd": x["quarter_end"], "unemployment": x["unemployment"], "hpiChange": x["hpi_change"],
                             "mortgageRate": x["mortgage_rate"], "expectedDefaults": x["expected_defaults"],
                             "expectedLossM": m(x["expected_loss"]), "lossRate": x["loss_rate_of_start_balance"]}
                            for x in q["quarters"]]} for s, q in stress["fed_quarterly"].items()}  # fmt: skip
    shocks = [{"shock": k, "label": names[k], "reserveM": m(v["reserve"]), "multiple": v["multiple_of_baseline"],
               "lossRate9q": v["loss_rate_9q_of_average_balance"], "worstHpiChange": v["worst_hpi_change"],
               "peakUnemployment": v["peak_unemployment"], "informationOnly": v["information_only"]}
              for k, v in stress["shocks"].items()]  # fmt: skip
    sens = [{"assumption": x["assumption"], "setting": x["setting"], "reserveM": m(x["weighted_reserve"]),
             "changeM": m(x["change"]), "changePct": x["change_pct"], "booked": x["booked"],
             "totalM": m(x["total_allowance"])} for x in stress["sensitivity"]]  # fmt: skip
    tornado = [{"assumption": t["assumption"], "lowM": m(t["low_change"]), "highM": m(t["high_change"]), "rangeM": m(t["range"])}
               for t in stress["tornado"]]  # fmt: skip
    b = stress["benchmark"]
    return header() | {"fed": fed, "shocks": shocks, "sensitivity": sens, "tornado": tornado,
                       "booked": {"performingM": m(stress["booked"]["weighted_reserve"]), "totalM": m(stress["booked"]["total_allowance"])},
                       "benchmark": {"ours": b["ours_severely_adverse_9q_loss_rate_of_average_balance"],
                                     "dfastAggregate": b["dfast_2026"]["aggregate_32_banks"], "dfastRange": b["dfast_2026"]["range"],
                                     "source": b["dfast_2026"]["source"]}}  # fmt: skip


def disclosures(r: dict[str, Any]) -> dict[str, Any]:
    def rf(x: dict[str, Any]) -> dict[str, Any]:
        conv = lambda row: {k: (row[k] if k == "group" else round(float(row[k]) / 1e6, 3)) for k in row}  # noqa: E731
        return {"rows": [conv(g) for g in x["by_group"]], "total": conv({"group": "Total", **x["total"]}),
                "reconciles": x["reconciles_to_the_cent"], "writeOffEvents": x["write_off_events"],
                "estimatedWriteOffEvents": x["estimated_write_off_events"],
                "totalCents": x["total"]}  # fmt: skip

    def br(x: dict[str, Any]) -> dict[str, Any]:
        return {"steps": [{"step": k, "amountM": m(v)} for k, v in x["steps"].items()], "reconciles": x["reconciles"],
                "tiesToTotalAllowance": x.get("ties_to_total_allowance", False)}  # fmt: skip

    d = r["disclosures"]
    table = lambda t: {band: {y: m(v) for y, v in row.items()} for band, row in t.items()}  # noqa: E731
    return header() | {
        "rollforward": {"q1_2026": rf(r["rollforward_q1_2026"]), "q2_2026": rf(r["rollforward_q2_2026"])},
        "bridge": {"q1_2026": br(r["bridge_q4_2025_to_q1_2026"]), "q2_2026": br(r["bridge_q1_to_q2_2026"])},
        "creditQualityFico": table(d["credit_quality_fico_by_origination_year"]),
        "creditQualityLtv": table(d["credit_quality_current_ltv_by_origination_year"]),
        "writeOffsQ2": {
            y: round(v / 1e6, 4) for y, v in d["gross_write_offs_by_origination_year_q2_2026"].items()
        },
        "writeOffsYtd": {
            y: round(v / 1e6, 4) for y, v in d["gross_write_offs_by_origination_year_ytd_2026"].items()
        },
    }


def methods(bt: dict[str, Any], l043: dict[str, Any]) -> dict[str, Any]:
    models = [
        {"id": "M-01", "name": "Default and payoff (PD and prepayment)", "how": "Monthly logistic transition models from the loan's delinquency state (current, 30, 60 days late), played forward by path simulation, 8 paths per loan", "status": "Challenger; holdout failed"},
        {"id": "M-02", "name": "Loss given default (LGD)", "how": "Two steps: the chance of any loss, then its size; monotone trees tied to the loan-to-value and house prices", "status": "Built; conservative (1.64x)"},
        {"id": "M-03", "name": "Exposure at default (EAD)", "how": "The payment schedule plus partial prepayment by loan age", "status": "Built; within 2% on held-out loans"},
        {"id": "M-04", "name": "CECL engine", "how": "PD x LGD x EAD for every loan and month to maturity; Fed scenarios published by the reserve date; 24-month forecast, 12-month reversion; weights 60/30/10", "status": "Built; tie-outs and rollforwards to the cent"},
        {"id": "M-05/06", "name": "Benchmarks", "how": "Loss-rate and vintage methods", "status": "Range checks"},
        {"id": "M-07", "name": "Adverse path", "how": "Halfway between the Fed's baseline and severely adverse paths", "status": "Built"},
    ]  # fmt: skip
    exams = [
        {"exam": "2011-2013", "recorded": 0.92, "corrected": 1.27}, {"exam": "2014-2016", "recorded": 1.09, "corrected": 1.37},
        {"exam": "2017-2019", "recorded": 1.09, "corrected": 1.35}, {"exam": "2008-2010 crisis", "recorded": 0.38, "corrected": 0.50},
        {"exam": "Holdout, Jul 2024 - Jun 2026", "recorded": 0.81, "corrected": 0.92},
    ]  # fmt: skip
    backtests: dict[str, Any] = {}
    if "crisis" in bt:
        v = bt["crisis"]["verdict"]
        backtests["crisis"] = {"defaultsRatio": v["defaults_ratio"], "lossRatio": v["loss_ratio"], "passes": v["passes"],
                               "benchmarkDefaultsRatio": v["benchmark_defaults_ratio"], "benchmarkLossRatio": v["benchmark_loss_ratio"]}  # fmt: skip
    if "rolling" in bt:
        rb = bt["rolling"]
        backtests["rolling"] = {"pooledRatio": rb["verdict_actual"]["pooled_ratio"], "passes": rb["verdict_actual"]["passes"],
                                "datesInBand": rb["verdict_actual"]["dates_in_band"],
                                "dates": [{"date": d[:4], "ratio": x["actual"]["loss_ratio"], "flatRatio": x["flat"]["loss_ratio"]}
                                          for d, x in rb["dates"].items()]}  # fmt: skip
    if "forbearance" in bt:
        v = bt["forbearance"]["verdict"]
        backtests["forbearance"] = {"bookedRatio": v["booked_ratio"], "flagIgnoredRatio": v["flag_ignored_ratio"],
                                    "curedShare": v["cured_share"], "passes": v["passes"]}  # fmt: skip
    limits = [
        {"id": "L-038", "text": "v2 failed its holdout: defaults short in 2024-2026, worst in the newest vintages."},
        {"id": "L-033", "text": "The LGD predicts 1.64 times the real loss per default (resolved 2017-2021 defaults)."},
        {"id": "L-039", "text": "The check used in the exams under-counted projected defaults; counted correctly, v2 over-projects in calm years."},
        {"id": "L-035", "text": "The crisis exam failed: like any data-driven model, v2 under-reacts to a kind of downturn it has not seen."},
        {"id": "L-040", "text": "After year 3 the loss runs at the month-36 chances and the long-run economy."},
        {"id": "L-001", "text": "Conforming Freddie Mac loans only: no subprime, jumbo, commercial or card loans."},
        {"id": "L-043", "text": "Mortgage-rate shocks lower the reserve slightly in both directions."},
    ]  # fmt: skip
    return header() | {"models": models, "exams": exams, "backtests": backtests, "limits": limits,
                       "definitionOfDefault": "90+ days late, a real-estate-owned acquisition, or a loss-event payoff; payment pauses for disasters and COVID stop the delinquency clock.",
                       "l043": bool(l043)}  # fmt: skip


def pipeline() -> dict[str, Any]:
    """The pipeline's shape and its controls (no identifiers: no hosts, accounts or workspace names)."""
    steps = [
        {"step": "Freddie Mac files", "what": "Origination and monthly performance files, read from the zips; data contracts per file version"},
        {"step": "Bronze / silver / gold", "what": "Delta tables on Databricks, DuckDB and Parquet on the PC; one row per loan per month in the panel"},
        {"step": "Economic data", "what": "FRED, FHFA house prices, BLS state unemployment, the Fed's scenarios; Airflow downloads, contracts and freshness checks"},
        {"step": "Models", "what": "PD and payoff transition models (MLflow), LGD and EAD; selection rules committed before each test"},
        {"step": "Engine", "what": "Each loan's lifetime expected loss under three scenarios, weighted; overlays only under ADR-006"},
        {"step": "Close", "what": "Rollforward to the cent, reserve bridge, disclosure tables, allowance memo with maker and checker sign-off"},
        {"step": "Publish", "what": "Aggregates only, after every check passes; no loan rows ever"},
    ]  # fmt: skip
    controls = [
        "Tie-outs on every run: loan counts and balances match the source; the book reserve equals the sum of loan reserves",
        "Data contracts per Freddie file version, per economic source and for the call reports; a failure stops the load",
        "Quarter-end snapshots: data (Delta time travel), model (MLflow) and code (git tag)",
        "Every test's rules committed before it runs; run-once tests never re-run without written approval",
        "Change log and limitations log with an owner and a fix date for every item",
        "No run on a Monday before 13:00 or between 02:30 and 04:00 (shared server); checked in CI and at every start",
    ]  # fmt: skip
    return header() | {
        "steps": steps,
        "controls": controls,
        "stack": [
            "Airflow",
            "Databricks (Delta, PySpark, MLflow)",
            "dbt",
            "DuckDB and Parquet",
            "Python package cecl",
            "GitHub Actions CI",
        ],
    }


def build(data_root: Path = DATA_ROOT) -> dict[str, dict[str, Any]]:
    """Every site data file, by file name (aggregates only)."""
    r = read("docs/phase4/reserve_2026Q2.json")
    comp = read("docs/phase4/reserve_components_2026Q2.json")
    stress = read("docs/phase5/stress_2026Q2.json")
    peer, bt, l043 = (
        read("docs/phase6/peer_ratios.json"),
        read("docs/phase6/backtests.json"),
        read("docs/phase6/l043_breakdown.json"),
    )
    saved = read(BY_GROUP)
    expl = (
        (header() | {k: v for k, v in saved.items() if k not in ("dataAsOf", "status")})
        if saved
        else explorer(data_root)
    )
    return {"overview.json": overview(r, comp, stress, peer), "explorer.json": expl,
            "scenarios.json": scenarios(stress), "disclosures.json": disclosures(r), "methods.json": methods(bt, l043),
            "pipeline.json": pipeline()}  # fmt: skip


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    groups = explorer()  # recomputed from the priced files on the PC, then saved for the VM
    (ROOT / BY_GROUP).write_text(json.dumps(groups, indent=1), encoding="utf-8")
    print(f"written {BY_GROUP}")
    for name, data in build().items():
        (OUT / name).write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"written site/public/data/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
