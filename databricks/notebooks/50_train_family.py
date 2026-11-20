# Databricks notebook source
# MAGIC %md
# MAGIC ### 50 train one model family (Phase 2 steps 3-5)
# MAGIC One family per job run (Phase 2 plan: a run stopped by the daily limit only redoes that family).
# MAGIC Thin wrapper around `cecl.models.pipeline`: fit on the training months (1999-2016; plus the COVID
# MAGIC months 2020 - June 2022 for the sensitivity run), evaluate on the validation months (2017-2019),
# MAGIC every measure weighted, and log everything to the dev MLflow experiment. The test months are
# MAGIC never read here (ADR-009 E).
# MAGIC
# MAGIC Parameters: `family` (logistic, lightgbm, ebm); `include_covid` (sensitivity run); `ebm_sample_every`
# MAGIC (EBM only: keep 1 in N of the training no-event months, re-weighted; documented in the run);
# MAGIC `tune`; `code_version` (the git commit, passed by whoever starts the job).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("family", "logistic"), ("include_covid", "false"),
                      ("ebm_sample_every", "1"), ("tune", "true"), ("settings", ""), ("code_version", "unknown"),
                      ("era", "none"), ("learning_curve", "false"), ("curve_fractions", "0.1,0.25,0.5,1.0"),
                      ("ebm_curve_outer_bags", "1"),
                      ("data_snapshot", "cecl-2026Q2-data-v1")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import curves, pipeline

environment.require_airflow_env()
GOLD = environment.databricks_schema("gold")
family = dbutils.widgets.get("family")
include_covid = dbutils.widgets.get("include_covid").lower() == "true"
ebm_every = int(dbutils.widgets.get("ebm_sample_every"))
tune = dbutils.widgets.get("tune").lower() == "true"
era = dbutils.widgets.get("era")  # ADR-010: none, binary (2009 on) or buckets (2005-2008, 2009 on)
feats = tuple(f.name for f in fx.model_features(era))
learning = dbutils.widgets.get("learning_curve").lower() == "true"
fractions = tuple(float(x) for x in dbutils.widgets.get("curve_fractions").replace(";", ",").split(",") if x)
settings = json.loads(dbutils.widgets.get("settings")) if dbutils.widgets.get("settings").strip() else None
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
print(environment.describe(), "| family:", family, "| covid in training:", include_covid)

# COMMAND ----------

wanted = ["loan_id", "vintage_year", "period_ym", "split", "outcome", "weight", *fx.FEATURE_NAMES]
cols = ", ".join(wanted)
train_splits = [fx.TRAIN] + ([fx.COVID] if include_covid else [])
in_list = ", ".join(f"'{s}'" for s in train_splits)
started = time.monotonic()
train = spark.sql(f"select {cols} from {GOLD}.features where split in ({in_list})").toPandas()
valid = spark.sql(f"select {cols} from {GOLD}.features where split = '{fx.VALIDATE}'").toPandas()
horizon = spark.sql(f"select {cols} from {GOLD}.features_horizon where split = '{fx.VALIDATE}'").toPandas()
stages = {"load": round((time.monotonic() - started) / 60, 2)}
print(f"rows: train {len(train):,}, validate {len(valid):,}, horizon {len(horizon):,}")
if family == "ebm" and ebm_every > 1:
    train_fit = pipeline.downsample_no_events(train, ebm_every)
    print(
        f"EBM trained on a smaller sample: 1 in {ebm_every} no-event months ({len(train_fit):,} rows), re-weighted"
    )
else:
    train_fit = train

t = time.monotonic()
# validation rows are passed only to record LightGBM's per-round curve (never to fit or tune)
model, tuning = pipeline.train(
    family, train_fit, tune=tune, settings=settings, features=feats, eval_frame=valid
)
stages["fit"] = round((time.monotonic() - t) / 60, 2)
t = time.monotonic()
result = pipeline.evaluate(model, valid, horizon, train, "201701")
stages["evaluate"] = round((time.monotonic() - t) / 60, 2)

# Training curves (training vs 2017-2019 validation; never the test months)
t = time.monotonic()
curve_data = {
    "fit": {
        "train": curves.train_scores(model, train_fit, pipeline.inputs),
        "valid": {k: result[k] for k in ("log_loss_default", "log_loss", "auc_default", "gini_default")},
    }
}
if getattr(model, "rounds", None):
    curve_data["rounds"] = model.rounds
if learning and family in ("logistic", "ebm"):
    chosen = dict(tuning["chosen"])
    if family == "ebm":
        chosen["outer_bags"] = int(
            dbutils.widgets.get("ebm_curve_outer_bags")
        )  # cheaper curve fits, recorded
    curve_data["learning"] = curves.learning_curve(
        lambda part: pipeline.train(family, part, settings=chosen, features=feats)[0],
        train_fit,
        valid,
        pipeline.inputs,
        fractions,
        full_model=model,
    )
    curve_data["learning_settings"] = chosen
stages["curves"] = round((time.monotonic() - t) / 60, 2)
minutes = round((time.monotonic() - started) / 60, 1)
tags = {"cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version"),
        "data_snapshot": dbutils.widgets.get("data_snapshot"),
        "sensitivity": "covid_in_training" if include_covid else "none"}  # fmt: skip
extra = {"train_splits": ",".join(train_splits), "ebm_sample_every": ebm_every if family == "ebm" else 1,
         "train_rows": len(train_fit), "validate_rows": len(valid), "minutes": minutes,
         "sample_every": fx.SAMPLE_EVERY, "sample_seed": fx.SAMPLE_SEED, "stage_minutes": stages,
         "era": era, "learning_curve": learning}  # fmt: skip
tags["era"] = era
extra["minutes"] = round((time.monotonic() - started) / 60, 1)
run_id = pipeline.log_run(mlflow, family, model, tuning, result, tags, extra, valid, curve_data)
summary = {"run_id": run_id, "family": family, "minutes": minutes, "stages": stages,
           **{k: result[k] for k in ("log_loss_default", "auc_default", "calibration_passes", "reason_codes")}}  # fmt: skip
print(json.dumps(summary, indent=1, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
