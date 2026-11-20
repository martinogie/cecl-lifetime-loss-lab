# Databricks notebook source
# MAGIC %md
# MAGIC ### 55 calibration diagnostics: score only, no training (Phase 2, 17 Nov 2026)
# MAGIC Loads the already-trained models from MLflow, scores the training (1999-2016) and validation
# MAGIC (2017-2019) rows, and runs `cecl.models.diagnostics`: AUC and calibration by delinquency status
# MAGIC at the start of the month; predicted / actual by calendar year; 2017-2018 in TX, FL and PR vs
# MAGIC the rest. Logs one MLflow run tagged `diagnostics`. Never reads the test months.
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
from cecl.models import diagnostics, pipeline

environment.require_airflow_env()
GOLD = environment.databricks_schema("gold")
runs = json.loads(dbutils.widgets.get("runs"))  # {"logistic": run_id, "lightgbm": run_id, ...}
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))

# COMMAND ----------

started = time.monotonic()
cols = ", ".join(["loan_id", "vintage_year", "period_ym", "split", "outcome", "weight", "property_state",
                  *fx.FEATURE_NAMES])  # fmt: skip
train = spark.sql(f"select {cols} from {GOLD}.features where split = '{fx.TRAIN}'").toPandas()
valid = spark.sql(f"select {cols} from {GOLD}.features where split = '{fx.VALIDATE}'").toPandas()
train_sample = diagnostics.training_sample(train)  # every default, 1 in 5 of the rest (re-weighted)
print(f"training rows scored: {len(train_sample):,} of {len(train):,}")
results = {}
for family, run_id in runs.items():
    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model
    results[family] = diagnostics.run_all(model, train_sample, valid, pipeline.inputs)
minutes = round((time.monotonic() - started) / 60, 1)

with mlflow.start_run(run_name="calibration-diagnostics"):
    mlflow.set_tags({"stage": "diagnostics", "phase": "2", "cecl_env": os.environ["CECL_ENV"]})
    mlflow.log_params({f"run_{k}": v for k, v in runs.items()} | {"minutes": minutes})
    for family, result in results.items():
        mlflow.log_dict(result, f"diagnostics_{family}.json")

summary = {
    family: {
        "auc_current": r["start_status_validation"]["current"]["auc_default"],
        "auc_delinquent": r["start_status_validation"]["delinquent"]["auc_default"],
        "gini_current": r["start_status_validation"]["current"]["gini_default"],
        "gini_delinquent": r["start_status_validation"]["delinquent"]["gini_default"],
        "ratio_current": r["start_status_validation"]["current"]["ratio"],
        "ratio_delinquent": r["start_status_validation"]["delinquent"]["ratio"],
        "hurricane_ratio": r["hurricanes_2017_2018"]["hurricane_states"]["ratio"],
        "other_ratio": r["hurricanes_2017_2018"]["other_states"]["ratio"],
    }
    for family, r in results.items()
}
print(json.dumps(summary, indent=1))
dbutils.notebook.exit(json.dumps({"minutes": minutes, "summary": summary}))
