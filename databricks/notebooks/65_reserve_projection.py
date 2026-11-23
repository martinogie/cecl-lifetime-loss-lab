# Databricks notebook source
# MAGIC %md
# MAGIC ### 65 reserve projection (Phase 4): v2 default and payoff chances for each reserve date and scenario
# MAGIC For each reserve month R (31 March and 30 June 2026), each scenario (baseline, adverse halfway path,
# MAGIC severely adverse; `cecl.scenarios`, ADR-003/005/008 with B4b) and each economy date (its own, plus the
# MAGIC 30 June book under the 31 March economy for the reserve bridge), the v2 `@challenger` models play every
# MAGIC loan alive at the end of R forward 36 months with the path simulation, starting from its **actual** state
# MAGIC at the end of R (`first_state`). The loan-level chances go to the bronze volume (never git); the PC prices
# MAGIC them with LGD and EAD (`scripts/build_reserve.py`). The scenario paths (aggregates) are saved alongside.
# MAGIC
# MAGIC Phase 5: `variants` (comma-separated keys of `cecl.stress.VARIANTS`; empty = the booked set-up) runs the
# MAGIC custom shocks and the sensitivities that need a new projection; their files carry a `__<variant>` suffix.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("runs", "{}"), ("plan", ""), ("paths", "8"),
                      ("forecast_months", "24"), ("reversion_months", "12"), ("variants", ""),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
import pandas as pd

from cecl import engine, environment, scenarios, stress
from cecl import features as fx
from cecl.models import simulate
from ingestion import reserve_book as rb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
runs = json.loads(dbutils.widgets.get("runs"))
paths = int(dbutils.widgets.get("paths"))
# ADR-003 settings (defaults 24 and 12); saved with every book so the PC pricing can check it uses the same
forecast_months = int(dbutils.widgets.get("forecast_months"))
reversion_months = int(dbutils.widgets.get("reversion_months"))
# plan: "book@economy" items, e.g. "202603@202603,202606@202606,202606@202603"
plan = [tuple(x.split("@")) for x in dbutils.widgets.get("plan").split(",") if x.strip()]
variants = [v.strip() for v in dbutils.widgets.get("variants").split(",") if v.strip()] or [""]
stress.check()
unknown = [v for v in variants if v and v not in stress.VARIANTS]
if unknown:
    raise ValueError(f"unknown variants: {unknown}")
mlflow.set_registry_uri("databricks-uc")
models = {fx.STATES.index(s): mlflow.pyfunc.load_model(f"runs:/{r}/model").unwrap_python_model().inner.model
          for s, r in runs.items()}  # fmt: skip
state_m = spark.table(f"{SILVER}.macro_state_monthly").toPandas()
nat_m = spark.table(f"{SILVER}.macro_national_monthly").toPandas()
# each economy date uses the Fed scenarios published by then (scenarios.fed_year): 2025 for Dec 2025
fed_by_year = {
    y: spark.table(f"{SILVER}.macro_{t}").toPandas()
    for y, t in scenarios.FED_TABLES.items()
    if any(scenarios.fed_year(e) == y for _, e in plan)
}
if any(v and stress.VARIANTS[v].timing == "calendar" for v in variants):
    fed_calendar = spark.table(f"{SILVER}.macro_{stress.FED_TABLE_CALENDAR}").toPandas()
long_run = spark.table(f"{SILVER}.macro_long_run_averages").toPandas()
KEEP = ["loan_id", "vintage_year", "property_state", "credit_score", "loan_age", "upb_start", "rate_start",
        "remaining_start", "orig_upb", "orig_ltv", "hpi_orig", "mi_pct", "occupancy_investor", "current_ltv"]  # fmt: skip


def reserve_book(R):
    """The book at R (ingestion/reserve_book.py, moved there unchanged for the close and monitoring)."""
    return rb.reserve_book(spark, R)


results, books = {}, {}
started = time.monotonic()
for R, E in plan:
    if R not in books:
        books[R] = reserve_book(R)
    start, history = books[R]
    # B4b weights: the book's balance by state at the reserve date of the economy's book
    w_book = books[E][0] if E in books else start
    weights = w_book.groupby("property_state")["upb_start"].sum().to_dict()
    overlap = set(weights) & set(state_m["state"].unique())
    if not overlap or sum(weights[s] for s in overlap) <= 0:
        raise ValueError(
            f"no book balance in the macro states: weights {list(weights)[:5]}, macro {sorted(state_m['state'].unique())[:5]}"
        )
    for variant in variants:
        spec = stress.VARIANTS[variant] if variant else None
        fm = spec.forecast_months if spec else forecast_months
        rv = spec.reversion_months if spec else reversion_months
        shock = scenarios.SHOCKS[spec.shock] if spec and spec.shock else None
        fed = fed_calendar if spec and spec.timing == "calendar" else fed_by_year[scenarios.fed_year(E)]
        for name in spec.scenarios if spec else scenarios.SCENARIOS:
            t = time.monotonic()
            sc = scenarios.build(
                name,
                E,
                state_m,
                nat_m,
                fed,
                long_run,
                weights,
                horizon=engine.MONTHLY + 3 + 12 * (int(R[:4]) - int(E[:4])) + int(R[4:]) - int(E[4:]),
                forecast_months=fm,
                reversion_months=rv,
                shock=shock,
            )
            proj = simulate.project(models, start, history, sc.macro, R, engine.MONTHLY + 1, None, paths=paths,
                                    first_state=start["first_state"].to_numpy(), first_pause=start["first_pause"].to_numpy(),
                                    payoff_multiplier=spec.payoff_multiplier if spec else 1.0)  # fmt: skip
            out = start[[c for c in KEEP if c in start.columns]].copy()
            out["first_state"] = start["first_state"].to_numpy()
            out["forecast_months"], out["reversion_months"] = fm, rv
            pdm = pd.DataFrame(
                proj.p_default[:, 1:], columns=[f"pd_{k}" for k in range(1, engine.MONTHLY + 1)]
            )
            ppm = pd.DataFrame(
                proj.p_prepay[:, 1:], columns=[f"pp_{k}" for k in range(1, engine.MONTHLY + 1)]
            )
            out = pd.concat([out.reset_index(drop=True), pdm, ppm], axis=1)
            tag = stress.tag(R, E, name, variant)
            os.makedirs(f"{VOLUME}/results/reserve", exist_ok=True)
            out.to_parquet(f"{VOLUME}/results/reserve/book_{tag}.parquet", index=False)
            sc.table().to_parquet(f"{VOLUME}/results/reserve/paths_{tag}.parquet", index=False)
            checks = {
                v: round(scenarios.weighted_change(sc, weights, v, 24), 6) for v in scenarios.STATE_VARIABLES
            }
            results[tag] = {"loans": len(out), "pd_36": float(proj.p_default[:, 1:].sum()),
                            "pp_36": float(proj.p_prepay[:, 1:].sum()),
                            "b4b_weighted_change_24": checks, "minutes": round((time.monotonic() - t) / 60, 2)}  # fmt: skip
            print(tag, results[tag])
results["minutes"] = round((time.monotonic() - started) / 60, 1)
dbutils.notebook.exit(json.dumps(results, default=float))
