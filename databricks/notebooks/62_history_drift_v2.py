# Databricks notebook source
# MAGIC %md
# MAGIC ### 62 simulated-history drift, model v2 (diagnosis for the next version; scoring only, L-038)
# MAGIC Why does the projection fall behind after the first year while the one-month predictions hold? This
# MAGIC replays two projections with a recorder (`cecl.models.drift.SimRecorder`; it changes nothing) and compares,
# MAGIC month by month, the live paths' state mix, memory inputs (months late in the past 12, worst state in 6
# MAGIC months, months since the first miss, state last month) and one-month chances with the same loans' real
# MAGIC rows in the transition tables (weighted):
# MAGIC - the holdout start, 1 July 2024, 24 months, v2's final models (the holdout has already run once; this
# MAGIC   only diagnoses it for the next version, nothing is re-chosen);
# MAGIC - F4, 1 January 2017, 36 months, the F4 exam models (trained to 2016).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import pickle
import time

for name, default in (
    ("env", ""),
    ("layout", "catalogs"),
    ("runs", "{}"),
    ("paths", "8"),
    ("code_version", "unknown"),
):
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import drift, multistate, simulate
from cecl.models import v2_pipeline as v2
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
runs = json.loads(dbutils.widgets.get("runs"))
paths = int(dbutils.widgets.get("paths"))
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
household = simulate.Household.from_table(spark.table(f"{SILVER}.macro_household_monthly").toPandas())
COLS = "t.period_ym, t.state_prev, t.state, t.weight, t.late_months_12, t.worst_state_6, t.months_since_first_miss, t.state_last_month"


def final_model(run_id):
    return mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model


def exam_model(run_id, exam):
    path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=f"exam_models/{exam}.pkl")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def actual_rows(start, table, first, last):
    spark.createDataFrame(start[["loan_id"]]).createOrReplaceTempView("drift_start_loans")
    return spark.sql(f"select {COLS} from {table} t join drift_start_loans s on t.loan_id = s.loan_id "
                     f"where t.period_ym between '{first}' and '{last}'").toPandas()  # fmt: skip


def run(label, models, start, history, s, months, table):
    rec = drift.SimRecorder(paths)
    t = time.monotonic()
    simulate.project(models, start, history, macro, s, months, household, paths=paths, observer=rec)
    rows = actual_rows(start, table, s, multistate.add_months(s, months - 1))
    table_ = drift.compare(rec.rows, drift.actual_by_month(rows, s, months))
    print(label, f"{len(start):,} loans, {round((time.monotonic() - t) / 60, 2)} min")
    return {
        "start": s,
        "months": months,
        "loans": len(start),
        "by_month": table_,
        "by_year": drift.by_year(table_),
    }


# COMMAND ----------

results = {}
# holdout start, rebuilt from the panel as the holdout run did
S = v2.HOLDOUT_START
parts = [spark.sql(fb.v2_projection_starts_sql(v, fb.databricks_relations(environment.databricks_schema, v), "spark", starts=(S,)))
         for v in range(1999, int(S[:4]) + 1)]  # fmt: skip
df = parts[0]
for p in parts[1:]:
    df = df.unionByName(p, allowMissingColumns=True)
start = df.toPandas().sort_values("loan_id").reset_index(drop=True)
history = spark.sql(fb.v2_projection_history_sql(f"{GOLD}.loan_panel", starts=(S,))).toPandas()
models = {fx.STATES.index(s): final_model(r) for s, r in runs.items()}
results["holdout_202407"] = run(
    "holdout", models, start, history, S, v2.HOLDOUT_MONTHS, f"{GOLD}.transitions_v2_holdout"
)

# F4: the stored start table and the F4 exam models
S4 = v2.EXAM_STARTS["F4"][0]
start = spark.sql(f"select * from {GOLD}.projection_v2_start where start_ym = '{S4}'").toPandas()
start = start.sort_values("loan_id").reset_index(drop=True)
history = spark.sql(f"select * from {GOLD}.projection_v2_history where start_ym = '{S4}'").toPandas()
models = {fx.STATES.index(s): exam_model(r, "F4") for s, r in runs.items()}
results["F4_201701"] = run("F4", models, start, history, S4, 36, f"{GOLD}.transitions_v2")

# COMMAND ----------

with mlflow.start_run(run_name="v2-history-drift"):
    mlflow.set_tags({"model_version": "v2", "stage": "diagnosis_history_drift", "for": "next model version (L-038)",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({"runs": json.dumps(runs), "paths": paths})
    mlflow.log_dict(results, "history_drift.json")
print(json.dumps({k: v["by_year"] for k, v in results.items()}, indent=1))
dbutils.notebook.exit(json.dumps({k: v["by_year"] for k, v in results.items()}))
