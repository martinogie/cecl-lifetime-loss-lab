# Databricks notebook source
# MAGIC %md
# MAGIC ### 52 train one transition model, model v2 (ADR-009 Amendment 3)
# MAGIC One family (logistic or LightGBM; the EBM is dropped for v2) for one starting state per run. Thin
# MAGIC wrapper around `cecl.models.v2_pipeline`:
# MAGIC 1. walk-forward tuning over the exams F1-F4 (train to 2007/2010/2013/2016, score 2008-10/2011-13/
# MAGIC    2014-16/2017-19), the step's log loss across all destinations; the chosen setting's exam models are kept;
# MAGIC 2. every bar on each exam's scored months; the final model fitted on all v2 training months (1999-2019
# MAGIC    and July 2022 - June 2024); the rate-lock band bar in-sample on July 2022 - June 2024;
# MAGIC 3. F5 (information): trained to 2019, scored on July 2022 - June 2024;
# MAGIC 4. everything to the dev MLflow experiment, tagged `model_version = v2`. The holdout months are never read.
# MAGIC
# MAGIC Modes: `train` (default); `screen` (household-stress keep rule, current-loan logistic); `covid`
# MAGIC (information: the given settings refitted with the COVID months added, and F5 trained to June 2022).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("family", "logistic"), ("start_state", "60"),
                      ("mode", "train"), ("stress", ""), ("settings", ""), ("code_version", "unknown"),
                      ("data_snapshot", "cecl-2026Q2-data-v1")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import transition_pipeline as tp
from cecl.models import transitions
from cecl.models import v2_pipeline as v2

environment.require_airflow_env()
GOLD = environment.databricks_schema("gold")
family = dbutils.widgets.get("family")
if family not in v2.FAMILIES:
    raise ValueError(f"model v2 families are {v2.FAMILIES} (the EBM is dropped for v2), not {family!r}")
start = transitions.start_code(dbutils.widgets.get("start_state"))
mode = dbutils.widgets.get("mode")
stress = tuple(s.strip() for s in dbutils.widgets.get("stress").replace(";", ",").split(",") if s.strip())
settings = json.loads(dbutils.widgets.get("settings")) if dbutils.widgets.get("settings").strip() else None
features = fx.v2_features(stress)
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
tags = {"cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version"),
        "data_snapshot": dbutils.widgets.get("data_snapshot"), "stage": mode}  # fmt: skip
print(environment.describe(), f"| v2 T-{fx.STATES[start]} {family} | mode {mode} | stress {stress}")

# COMMAND ----------

splits = ["train", "covid"] if mode == "covid" else ["train"]
wanted = ["loan_id", "vintage_year", "period_ym", "split", "state_prev", "state", "weight",
          *fx.V2_STORED, *(f.name for f in fx.V2_STRESS)]  # fmt: skip
cols = ", ".join(dict.fromkeys(wanted))
in_list = ", ".join(f"'{s}'" for s in splits)
started = time.monotonic()
query = f"select {cols} from {GOLD}.transitions_v2 where state_prev = {start} and split in ({in_list})"
frame = spark.sql(query).toPandas()
frame = v2.add_derived(frame, fx.V2_BASE_FEATURES)
stages = {"load": round((time.monotonic() - started) / 60, 2)}
print(f"rows: {len(frame):,} ({', '.join(splits)})")

if mode == "screen":
    rec = v2.screen_stress(frame, tuple(f.name for f in fx.V2_STRESS), start=start)
    with mlflow.start_run(run_name="v2-stress-screen") as run:
        mlflow.set_tags(
            tags | {"model_version": "v2", "stage": "stress_screen", "transition": fx.STATES[start]}
        )
        mlflow.log_dict(rec, "stress_screen.json")
        mlflow.log_metric("minutes", round((time.monotonic() - started) / 60, 1))
    out = {"run_id": run.info.run_id, "kept": rec["kept"], "minutes": round((time.monotonic() - started) / 60, 1),
           "base_average": rec["base_average"],
           "candidates": {c: {k: r[k] for k in ("average_gain", "worst_exam_loss", "eligible", "kept")}
                          for c, r in rec["candidates"].items()}}  # fmt: skip
    print(json.dumps(out, indent=1, default=str))
    dbutils.notebook.exit(json.dumps(out, default=str))

# COMMAND ----------

if mode == "covid":
    chosen = settings or {}
    final = v2.fit(family, start, frame, chosen, features)
    info_model, info = v2.info_exam(
        family, start, frame, chosen, features, fold=("202206", "202207", "202406")
    )
    train_only = frame[frame["split"] == "train"]
    base_model, base_info = v2.info_exam(family, start, train_only, chosen, features)
    artifacts = {"covid_info_exam": info, "without_covid_info_exam": base_info}
    metrics_ = {"info_log_loss_with_covid": info.get("log_loss", float("nan")),
                "info_log_loss_without_covid": base_info.get("log_loss", float("nan")),
                "minutes": round((time.monotonic() - started) / 60, 1)}  # fmt: skip
    run_id = v2.log_run(mlflow, final, {"chosen": chosen, "fixed": True}, tags | {"sensitivity": "covid_in_training"},
                        {"train_splits": "train,covid"}, artifacts, {"F5_with_covid": info_model}, frame, metrics_)  # fmt: skip
    out = {"run_id": run_id, **metrics_}
    print(json.dumps(out, indent=1))
    dbutils.notebook.exit(json.dumps(out))

# COMMAND ----------

t = time.monotonic()
if settings is not None:
    record, exam_models = v2.tune(family, start, frame, features, grid=[settings])
    record["fixed"] = True
else:
    record, exam_models = v2.tune(family, start, frame, features)
stages["tune"] = round((time.monotonic() - t) / 60, 2)
t = time.monotonic()
exams = v2.exam_results(exam_models, frame)
final = v2.fit(family, start, frame, record["chosen"], features)
lock = v2.lock_band_bar(final, frame)
p = v2.period(frame)
recent = frame[(p >= v2.IN_SAMPLE_LOCK[0]) & (p <= v2.IN_SAMPLE_LOCK[1])]
in_sample = tp.evaluate(final, recent, frame, period_label="in-sample 2022-2024")
info_model, info = v2.info_exam(family, start, frame, record["chosen"], features)
stages["evaluate"] = round((time.monotonic() - t) / 60, 2)
minutes = round((time.monotonic() - started) / 60, 1)
result = {"average": record["chosen_average"], "exams": exams, "lock_band": lock,
          "reason_codes": in_sample["reason_codes"]}  # fmt: skip
summary = {"transition": fx.STATES[start], "family": family, "minutes": minutes, "stages": stages,
           "chosen": record["chosen"], "exam_log_loss": [e["log_loss"] for e in exams],
           "average_exam_log_loss": record["chosen_average"],
           "exam_bars_pass": [e["calibration_passes"] for e in exams], "lock_band_passes": lock["passes"],
           "reason_codes": in_sample["reason_codes"], "bars_pass": v2.bars_pass(result),
           "failing_cells": v2.failing_cells(result)[:60], "info_exam_log_loss": info.get("log_loss")}  # fmt: skip
metrics_ = {"average_exam_log_loss": record["chosen_average"], "bars_pass": float(summary["bars_pass"]),
            "reason_codes": float(bool(in_sample["reason_codes"])), "lock_band_passes": float(lock["passes"]),
            "in_sample_log_loss": in_sample["log_loss"], "minutes": minutes,
            **{f"exam_{i + 1}_log_loss": e["log_loss"] for i, e in enumerate(exams)}}  # fmt: skip
if info:
    metrics_["info_exam_log_loss"] = info["log_loss"]
models = {f"F{i + 1}": m for i, m in enumerate(exam_models)} | {"F5_info": info_model}
artifacts = {"exams": exams, "lock_band": lock, "in_sample_2022_2024": in_sample, "info_exam_F5": info,
             "summary": summary}  # fmt: skip
params = {"stress": list(stress), "features": list(features), "train_splits": "train", "train_rows": len(frame),
          "sample_every": fx.SAMPLE_EVERY, "stage_minutes": stages}  # fmt: skip
run_id = v2.log_run(mlflow, final, record, tags, params, artifacts, models, frame, metrics_)
summary["run_id"] = run_id
print(json.dumps(summary, indent=1, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
