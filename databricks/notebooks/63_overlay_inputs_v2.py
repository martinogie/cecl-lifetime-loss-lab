# Databricks notebook source
# MAGIC %md
# MAGIC ### 63 overlay sizing inputs (Phase 4, L-038): v2's monthly default chances for the 1 July 2024 book
# MAGIC Reproduces the holdout projection (same models, same seed, same economy) only to save each loan's
# MAGIC projected default chance by month, which the holdout run did not keep. Nothing is judged here: the
# MAGIC projected total is checked against the recorded holdout run, and the job stops if it differs. The
# MAGIC output (loan-level, so it stays in the bronze volume and the git-ignored local folder, never in git) is
# MAGIC priced with the Phase 3 LGD and EAD on the PC (`scripts/size_overlay_v2.py`).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os

for name, default in (("env", ""), ("layout", "catalogs"), ("runs", "{}"), ("expected_total", ""),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
import pandas as pd

from cecl import environment
from cecl import features as fx
from cecl.models import multistate, simulate
from cecl.models import v2_pipeline as v2
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
runs = json.loads(dbutils.widgets.get("runs"))
expected = float(dbutils.widgets.get("expected_total"))
mlflow.set_registry_uri("databricks-uc")
S, MONTHS = v2.HOLDOUT_START, v2.HOLDOUT_MONTHS
parts = [spark.sql(fb.v2_projection_starts_sql(v, fb.databricks_relations(environment.databricks_schema, v), "spark", starts=(S,)))
         for v in range(1999, int(S[:4]) + 1)]  # fmt: skip
df = parts[0]
for p in parts[1:]:
    df = df.unionByName(p, allowMissingColumns=True)
start = df.toPandas().sort_values("loan_id").reset_index(drop=True)
history = spark.sql(fb.v2_projection_history_sql(f"{GOLD}.loan_panel", starts=(S,))).toPandas()
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
household = simulate.Household.from_table(spark.table(f"{SILVER}.macro_household_monthly").toPandas())
models = {fx.STATES.index(s): mlflow.pyfunc.load_model(f"runs:/{r}/model").unwrap_python_model().inner.model
          for s, r in runs.items()}  # fmt: skip
proj = simulate.project(models, start, history, macro, S, MONTHS, household, paths=8)
total = float(proj.p_default.sum())
if abs(total - expected) > 1e-6 * expected:
    raise ValueError(
        f"projected total {total:.4f} differs from the recorded holdout run {expected:.4f}: stopping"
    )

keep = ["loan_id", "vintage_year", "state_prev", "property_state", "credit_score", "loan_age", "upb_start",
        "rate_start", "remaining_start", "orig_upb", "orig_ltv", "hpi_orig"]  # fmt: skip
out = start[[c for c in keep if c in start.columns]].copy()
pd_cols = pd.DataFrame(proj.p_default, columns=[f"pd_{m}" for m in proj.months])
out = pd.concat([out.reset_index(drop=True), pd_cols], axis=1)
os.makedirs(f"{VOLUME}/results/v2", exist_ok=True)
out.to_parquet(f"{VOLUME}/results/v2/holdout_202407_p_default.parquet", index=False)
print(f"{len(out):,} loans; projected defaults {total:,.2f} (matches the holdout run)")
dbutils.notebook.exit(
    json.dumps({"loans": len(out), "total": total, "path": "results/v2/holdout_202407_p_default.parquet"})
)
