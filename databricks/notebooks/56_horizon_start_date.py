# Databricks notebook source
# MAGIC %md
# MAGIC ### 56 horizon AUC from start-date information only (scoring only, no training; 17 Nov 2026)
# MAGIC Re-reports AUC and Gini at 12, 24 and 36 months for the already-trained models on the validation
# MAGIC horizon table (2017-2019), using `cecl.models.pipeline.horizon_from_start`: each loan is scored
# MAGIC from its January 2017 row only and projected forward (age and calendar month advance; every other
# MAGIC input stays at its start value). Adds `val_auc_start_<h>m` / `val_gini_start_<h>m` to each model's
# MAGIC validation run and logs one run tagged `horizon_start_date`. Never reads the test months.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("runs", "{}")):
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import pipeline

environment.require_airflow_env()
GOLD = environment.databricks_schema("gold")
runs = json.loads(dbutils.widgets.get("runs"))
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))

# COMMAND ----------

started = time.monotonic()
cols = ", ".join(["loan_id", "vintage_year", "period_ym", "split", "outcome", "weight", *fx.FEATURE_NAMES])
horizon = spark.sql(f"select {cols} from {GOLD}.features_horizon where split = '{fx.VALIDATE}'").toPandas()
client = mlflow.MlflowClient()
results = {}
for family, run_id in runs.items():
    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model
    results[family] = pipeline.horizon_from_start(model, horizon, "201701")
    for h, v in results[family].items():
        client.log_metric(run_id, f"val_auc_start_{h}m", v["auc"])
        client.log_metric(run_id, f"val_gini_start_{h}m", v["gini"])
minutes = round((time.monotonic() - started) / 60, 1)
with mlflow.start_run(run_name="horizon-start-date"):
    mlflow.set_tags({"stage": "horizon_start_date", "phase": "2", "cecl_env": os.environ["CECL_ENV"]})
    mlflow.log_params({f"run_{k}": v for k, v in runs.items()} | {"minutes": minutes, "start": "201701"})
    mlflow.log_dict(results, "horizon_start_date.json")
print(json.dumps(results, indent=1))
dbutils.notebook.exit(json.dumps({"minutes": minutes, "results": results}))
