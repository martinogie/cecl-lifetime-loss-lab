# Databricks notebook source
# MAGIC %md
# MAGIC ### 66 Phase 6 backtests: projections (run once each; rules in docs/phase6/test_designs.md)
# MAGIC - `crisis`: the v2 recipe (logistic, C = 1.0, the 28 v2 inputs) refitted on months up to December 2006;
# MAGIC   the book alive at 31 December 2006 played forward 48 months on the economy as it happened.
# MAGIC - `rolling`: at each year-end in `dates`, the same recipe refitted on months up to that date (COVID months
# MAGIC   left out, as for v2); the book alive then played forward 36 months on (a) the economy as it happened and
# MAGIC   (b) flat then reverted (`cecl.backtest.flat_macro`, data up to the date only).
# MAGIC - `forbearance`: the booked v2 models; the book alive at 30 June 2020, starting states (A) with the
# MAGIC   stop-the-clock rule (booked) and (B) with the forbearance flag ignored; 36 months, economy as it happened.
# MAGIC Each projection starts from each loan's actual state (`first_state`), 8 paths, seed 7. The loan-level
# MAGIC chances go to the bronze volume (never git); the PC refits the LGD, prices, and scores against actuals
# MAGIC (`scripts/build_backtests.py`).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import pickle
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("test", ""), ("dates", ""), ("runs", "{}"),
                      ("paths", "8"), ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
import pandas as pd

from cecl import backtest, environment
from cecl import features as fx
from cecl.models import multistate, simulate
from cecl.models import v2_pipeline as v2
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
OUT = f"{VOLUME}/results/backtest"
test = dbutils.widgets.get("test").strip()
if test not in ("crisis", "rolling", "forbearance"):
    raise ValueError(f"test must be crisis, rolling or forbearance, got {test!r}")
paths = int(dbutils.widgets.get("paths"))
dates = [d.strip() for d in dbutils.widgets.get("dates").split(",") if d.strip()]
runs = json.loads(dbutils.widgets.get("runs"))
SETTINGS = {
    "C": 1.0
}  # v2's chosen setting at all three steps (docs/phase2/model_v2_selection.json); not re-tuned
FEATURES = fx.V2_BASE_FEATURES
COVID = (fx.V2_SPLITS[1][1], fx.V2_SPLITS[1][2])  # left out of fitting, as for v2
actual = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                      spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
KEEP = ["loan_id", "vintage_year", "property_state", "credit_score", "loan_age", "upb_start", "rate_start",
        "remaining_start", "orig_upb", "orig_ltv", "hpi_orig", "mi_pct", "occupancy_investor", "current_ltv"]  # fmt: skip
os.makedirs(OUT, exist_ok=True)


def refit(as_of: str) -> dict:
    """The three logistic transition models fitted on months up to `as_of` only (COVID months left out)."""
    cols = ", ".join(dict.fromkeys(["state_prev", "state", "weight", "period_ym", *fx.V2_STORED]))
    models = {}
    for s in fx.LIVE_STATES:
        frame = spark.sql(f"select {cols} from {GOLD}.transitions_v2 where state_prev = {s} and period_ym <= '{as_of}' "
                          f"and not (period_ym between '{COVID[0]}' and '{COVID[1]}')").toPandas()  # fmt: skip
        frame = v2.add_derived(frame, FEATURES)
        models[s] = v2.fit("logistic", s, frame, SETTINGS, FEATURES)
        print(as_of, fx.STATES[s], "fitted on", len(frame), "rows")
    with open(f"{OUT}/models_{test}_{as_of}.pkl", "wb") as fh:  # kept for the record
        pickle.dump(models, fh)
    return models


def book(R: str):
    """Loans alive at the end of R: start row, history, and both starting-state readings."""
    parts = [spark.sql(fb.v2_projection_starts_sql(v, fb.databricks_relations(environment.databricks_schema, v), "spark", starts=(R,)))
             for v in range(1999, int(R[:4]) + 1)]  # fmt: skip
    df = parts[0]
    for p in parts[1:]:
        df = df.unionByName(p, allowMissingColumns=True)
    start = df.toPandas()
    end = spark.sql(f"select loan_id, status, least(effective_months_late, 2) as first_state, "
                    f"least(coalesce(reported_months_late, effective_months_late), 2) as first_state_raw, "
                    f"case when flagged_pause then 1 else 0 end as first_pause from {GOLD}.loan_panel "
                    f"where period_ym = '{R}'").toPandas()  # fmt: skip
    alive = end["status"].isin(["current", "delinquent"])
    start = start.merge(
        end[alive][["loan_id", "first_state", "first_state_raw", "first_pause"]], on="loan_id"
    )
    if start.empty:
        raise ValueError(f"book for {R} is empty: stopping")
    start = start.sort_values("loan_id").reset_index(drop=True)
    history = spark.sql(fb.v2_projection_history_sql(f"{GOLD}.loan_panel", starts=(R,))).toPandas()
    return start, history


def project(models, start, history, macro, R, months, tag, first_state, first_pause):
    t = time.monotonic()
    proj = simulate.project(models, start, history, macro, R, months + 1, None, paths=paths,
                            first_state=first_state, first_pause=first_pause)  # fmt: skip
    out = start[[c for c in KEEP if c in start.columns]].copy()
    out["first_state"] = first_state
    pdm = pd.DataFrame(proj.p_default[:, 1:], columns=[f"pd_{k}" for k in range(1, months + 1)])
    ppm = pd.DataFrame(proj.p_prepay[:, 1:], columns=[f"pp_{k}" for k in range(1, months + 1)])
    pd.concat([out.reset_index(drop=True), pdm, ppm], axis=1).to_parquet(
        f"{OUT}/book_{tag}.parquet", index=False
    )
    res = {"loans": len(out), "pd_sum": float(proj.p_default[:, 1:].sum()), "pp_sum": float(proj.p_prepay[:, 1:].sum()),
           "minutes": round((time.monotonic() - t) / 60, 2)}  # fmt: skip
    print(tag, res)
    return res


# COMMAND ----------

started = time.monotonic()
results = {}
if test == "crisis":
    R = "200612"
    models = refit(R)
    start, history = book(R)
    results[R] = project(models, start, history, actual, R, 48, f"crisis_{R}", start["first_state"].to_numpy(),
                         start["first_pause"].to_numpy())  # fmt: skip
elif test == "rolling":
    for R in dates:
        t = time.monotonic()
        models = refit(R)
        start, history = book(R)
        fs, fp = start["first_state"].to_numpy(), start["first_pause"].to_numpy()
        results[R] = {
            "actual": project(models, start, history, actual, R, 36, f"rolling_{R}_actual", fs, fp),
            "flat": project(models, start, history, backtest.flat_macro(actual, R, 36 + 3), R, 36,
                            f"rolling_{R}_flat", fs, fp),
            "minutes": round((time.monotonic() - t) / 60, 2),
        }  # fmt: skip
else:  # forbearance: booked v2 models, starting states with and without the stop-the-clock rule
    R = "202006"
    mlflow.set_registry_uri("databricks-uc")
    models = {fx.STATES.index(s): mlflow.pyfunc.load_model(f"runs:/{r}/model").unwrap_python_model().inner.model
              for s, r in runs.items()}  # fmt: skip
    start, history = book(R)
    raw = start["first_state_raw"].to_numpy()
    results["A_booked"] = project(models, start, history, actual, R, 36, f"forbearance_{R}_A",
                                  start["first_state"].to_numpy(), start["first_pause"].to_numpy())  # fmt: skip
    results["B_flag_ignored"] = project(models, start, history, actual, R, 36, f"forbearance_{R}_B",
                                        raw, 0 * start["first_pause"].to_numpy())  # fmt: skip
    results["loans_late_only_under_B"] = int(((raw > 0) & (start["first_state"].to_numpy() == 0)).sum())
results["minutes"] = round((time.monotonic() - started) / 60, 1)
dbutils.notebook.exit(json.dumps(results, default=float))
