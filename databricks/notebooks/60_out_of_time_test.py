# Databricks notebook source
# MAGIC %md
# MAGIC ### 60 the out-of-time test, once (Phase 2 step 7, ADR-009 E)
# MAGIC Scores July 2022 - June 2026 (the holdout tables) for the chosen family, the other families (for
# MAGIC information) and the COVID sensitivity run, with every measure weighted, and logs one MLflow run
# MAGIC tagged `out_of_time_test` with the commit of the accepted ADR-009. Started only by
# MAGIC `scripts/run_oot_test.py`, which checks ADR-009 first.
# MAGIC
# MAGIC **Once:** if any `out_of_time_test` run already exists in the experiment, this stops without
# MAGIC scoring anything. The results are recorded whatever they show; no model is tuned on them.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os

for name, default in (("env", ""), ("layout", "catalogs"), ("runs", "{}"), ("chosen", ""), ("adr_009_commit", ""),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import families, pipeline, reasons

environment.require_airflow_env()
GOLD = environment.databricks_schema("gold")
runs = json.loads(dbutils.widgets.get("runs"))  # {"logistic": run_id, ..., "sensitivity_covid": run_id}
chosen, adr_commit = dbutils.widgets.get("chosen"), dbutils.widgets.get("adr_009_commit")
if not adr_commit or chosen not in runs:
    raise ValueError(
        "the test needs the ADR-009 commit and the chosen family's run (scripts/run_oot_test.py)"
    )
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
experiment = mlflow.set_experiment(environment.mlflow_experiment_path(user))
earlier = mlflow.search_runs(
    [experiment.experiment_id], "tags.stage = 'out_of_time_test'", output_format="list"
)
if earlier:
    raise RuntimeError(
        f"the out-of-time test has already run ({earlier[0].info.run_id}); it runs once (ADR-009 E)"
    )

# COMMAND ----------

cols = ", ".join(["loan_id", "vintage_year", "period_ym", "split", "outcome", "weight", *fx.FEATURE_NAMES])
test = spark.sql(f"select {cols} from {GOLD}.features_holdout").toPandas()
horizon = spark.sql(f"select {cols} from {GOLD}.features_horizon_holdout").toPandas()
baseline = (
    spark.sql(f"select {cols} from {GOLD}.features where split = '{fx.TRAIN}'").sample(0.1, seed=7).toPandas()
)
print(f"rows: test {len(test):,}, horizon {len(horizon):,}, training baseline for PSI {len(baseline):,}")

results = {}
for label, run_id in runs.items():
    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model
    results[label] = pipeline.evaluate(model, test, horizon, baseline, "202207")
    if label == chosen:
        sample = test.head(5000)
        codes = reasons.top_reasons(
            model.contributions(pipeline.inputs(model, sample)),
            pipeline.inputs(model, sample),
            features=families.feature_list(model),
        )
        reason_examples = codes[:50]

with mlflow.start_run(run_name="out-of-time-test") as run:
    mlflow.set_tags({"stage": "out_of_time_test", "adr_009_commit": adr_commit, "chosen": chosen, "phase": "2",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({f"run_{k}": v for k, v in runs.items()} | {"test_months": "202207-202606"})
    for label, result in results.items():
        mlflow.log_metrics(pipeline.scalar_metrics(result, f"test_{label}"))
        mlflow.log_dict(result, f"test_{label}.json")
    mlflow.log_dict(
        {"examples": reason_examples, "wording": reasons.wording_table()}, "reason_codes_chosen.json"
    )
    test_run = run.info.run_id

summary = {"test_run": test_run, "chosen": chosen,
           **{f"{k}_log_loss_default": v["log_loss_default"] for k, v in results.items()},
           **{f"{k}_calibration_passes": v["calibration_passes"] for k, v in results.items()}}  # fmt: skip
print(json.dumps(summary, indent=1, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
