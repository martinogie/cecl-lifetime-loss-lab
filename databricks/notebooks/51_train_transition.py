# Databricks notebook source
# MAGIC %md
# MAGIC ### 51 train one transition model (ADR-011; Phase 2 plan, multi-state, M4)
# MAGIC One family for one starting state per job run (a run stopped by a cut-off only redoes that model).
# MAGIC Thin wrapper around `cecl.models.transition_pipeline`: walk-forward tuning on the training years
# MAGIC only (three folds, the step's own log loss across all destinations; ADR-009 Amendment 1), refit on
# MAGIC 1999-2016 (plus the COVID months for the sensitivity run), evaluate on 2017-2019, training curves
# MAGIC and the fit table, everything logged to the dev MLflow experiment. The test months are never read.
# MAGIC
# MAGIC Parameters: `family` (logistic, lightgbm, ebm); `start_state` (current, 30, 60); `include_covid`;
# MAGIC `ebm_sample_every` and `ebm_sample_states` (EBM only: keep 1 in N of the rows ending in those
# MAGIC states, re-weighted, recorded); `tune`; `settings` (fixed settings, skips tuning); `learning_curve`;
# MAGIC `curve_fractions`; `ebm_curve_outer_bags`; `code_version` (the git commit, set by scripts/run_job.py).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("family", "logistic"), ("start_state", "60"),
                      ("include_covid", "false"), ("ebm_sample_every", "1"), ("ebm_sample_states", "stay;prepaid"),
                      ("tune", "true"), ("settings", ""), ("learning_curve", "true"),
                      ("curve_fractions", "0.1;0.25;0.5;1.0"), ("ebm_curve_outer_bags", "1"),
                      ("code_version", "unknown"), ("data_snapshot", "cecl-2026Q2-data-v1")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import transition_pipeline as tp
from cecl.models import transitions

environment.require_airflow_env()
GOLD = environment.databricks_schema("gold")
family = dbutils.widgets.get("family")
start = transitions.start_code(dbutils.widgets.get("start_state"))
include_covid = dbutils.widgets.get("include_covid").lower() == "true"
ebm_every = int(dbutils.widgets.get("ebm_sample_every"))
thin_names = [
    s.strip() for s in dbutils.widgets.get("ebm_sample_states").replace(";", ",").split(",") if s.strip()
]
thin_states = tuple(start if s == "stay" else fx.STATES.index(s) for s in thin_names)
do_tune = dbutils.widgets.get("tune").lower() == "true"
learning = dbutils.widgets.get("learning_curve").lower() == "true"
fractions = tuple(float(x) for x in dbutils.widgets.get("curve_fractions").replace(";", ",").split(",") if x)
settings = json.loads(dbutils.widgets.get("settings")) if dbutils.widgets.get("settings").strip() else None
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
print(environment.describe(), f"| T-{fx.STATES[start]} {family} | covid in training: {include_covid}")

# COMMAND ----------

cols = ", ".join(["loan_id", "vintage_year", "period_ym", "split", "state_prev", "state", "weight",
                  *fx.TRANSITION_FEATURES])  # fmt: skip
train_splits = [fx.TRAIN] + ([fx.COVID] if include_covid else [])
in_list = ", ".join(f"'{s}'" for s in train_splits)
started = time.monotonic()
base = f"select {cols} from {GOLD}.transitions where state_prev = {start}"
train = spark.sql(f"{base} and split in ({in_list})").toPandas()
valid = spark.sql(f"{base} and split = '{fx.VALIDATE}'").toPandas()
stages = {"load": round((time.monotonic() - started) / 60, 2)}
print(f"rows: train {len(train):,}, validate {len(valid):,}")
if family == "ebm" and ebm_every > 1:
    train_fit = tp.downsample(train, ebm_every, thin_states)
    print(f"EBM trained on a smaller sample: 1 in {ebm_every} of the rows ending in {thin_names} "
          f"({len(train_fit):,} rows), re-weighted")  # fmt: skip
else:
    train_fit = train

t = time.monotonic()
# walk-forward tuning happens inside train() on the training rows only; the validation rows are passed
# only to record LightGBM's per-round curve (never to fit or tune)
model, tuning = tp.train(family, start, train_fit, settings=settings, do_tune=do_tune, eval_frame=valid)
stages["tune_and_fit"] = round((time.monotonic() - t) / 60, 2)
t = time.monotonic()
result = tp.evaluate(model, valid, train)
stages["evaluate"] = round((time.monotonic() - t) / 60, 2)

# Training curves and the fit table (training vs 2017-2019; never the test months)
t = time.monotonic()
curve_data = {"fit": {"train": tp.train_scores(model, train_fit), "valid": tp.scores(model, valid)}}
if getattr(model, "rounds", None):
    curve_data["rounds"] = model.rounds
if learning and family in ("logistic", "ebm"):
    chosen = dict(tuning["chosen"])
    if family == "ebm":
        chosen["outer_bags"] = int(
            dbutils.widgets.get("ebm_curve_outer_bags")
        )  # cheaper curve fits, recorded
    curve_data["learning"] = tp.learning_curve(lambda part: tp.fit(family, start, part, chosen), train_fit, valid,
                                               fractions, full_model=model)  # fmt: skip
    curve_data["learning_settings"] = chosen
stages["curves"] = round((time.monotonic() - t) / 60, 2)
minutes = round((time.monotonic() - started) / 60, 1)
tags = {"cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version"),
        "data_snapshot": dbutils.widgets.get("data_snapshot"),
        "sensitivity": "covid_in_training" if include_covid else "none"}  # fmt: skip
extra = {"train_splits": ",".join(train_splits), "train_rows": len(train_fit), "validate_rows": len(valid),
         "ebm_sample": f"1 in {ebm_every} of {thin_names}" if family == "ebm" and ebm_every > 1 else "none",
         "sample_every": fx.SAMPLE_EVERY, "sample_seed": fx.SAMPLE_SEED, "stage_minutes": stages,
         "learning_curve": learning, "minutes": minutes, "tuning": "walk-forward, 3 folds" if do_tune else "none"}  # fmt: skip
run_id = tp.log_run(mlflow, model, tuning, result, tags, extra, valid, curve_data)
summary = {"run_id": run_id, "transition": fx.STATES[start], "family": family, "minutes": minutes, "stages": stages,
           "chosen": tuning.get("chosen"), "fold_average": tuning.get("average"),
           "log_loss": result["log_loss"], "calibration_passes": result["calibration_passes"],
           "reason_codes": result["reason_codes"],
           "fit_verdict": tp.verdict(curve_data["fit"]["train"], curve_data["fit"]["valid"])}  # fmt: skip
print(json.dumps(summary, indent=1, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
