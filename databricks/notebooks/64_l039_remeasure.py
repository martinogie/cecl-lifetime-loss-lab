# Databricks notebook source
# MAGIC %md
# MAGIC ### 64 L-039 re-measurement, for the record only
# MAGIC The projection check under-counted projected defaults: it cut each loan's projected chances at its actual
# MAGIC exit, including a payoff or default (L-039). This replays the recorded projections exactly (same models,
# MAGIC seeds and data) and scores each one twice: the **recorded rule** (`cut_at_exit=True`, which must reproduce
# MAGIC the recorded totals) and the **corrected count** (`cut_at_exit=False`: cut only where the outcome is
# MAGIC unknown). No verdict, selection or model changes; the corrected numbers are disclosed next to the originals.
# MAGIC - v2 walk-forward exams F1-F5 (exam models; F1 with the v1-style benchmark);
# MAGIC - v2 holdout (final models);
# MAGIC - v1 out-of-time test (v1's locked models, its slice projection, 48 months from July 2022).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import pickle
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("v2_runs", "{}"), ("v1_runs", "{}"),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import multistate, oot, simulate
from cecl.models import transition_pipeline as tp
from cecl.models import v2_pipeline as v2
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
v2_runs, v1_runs = json.loads(dbutils.widgets.get("v2_runs")), json.loads(dbutils.widgets.get("v1_runs"))
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
household = simulate.Household.from_table(spark.table(f"{SILVER}.macro_household_monthly").toPandas())


def pyfunc_model(run_id):
    return mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model


def exam_model(run_id, exam):
    name = "F5_info" if exam == "F5" else exam
    with open(
        mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=f"exam_models/{name}.pkl"), "rb"
    ) as fh:
        return pickle.load(fh)


def summary(c):
    years = {x.group: round(x.ratio, 4) for x in c.cells if x.event == "default" and x.by == "year"}
    vint = {
        x.group: round(x.ratio, 4)
        for x in c.cells
        if x.event == "default" and x.by == "vintage" and x.applies
    }
    failing = [
        f"{x.by} {x.group} {x.ratio:.2f}"
        for x in c.cells
        if x.event == "default" and x.applies and not x.passes
    ]
    t = c.totals
    return {"default_actual": t["default_actual"], "default_projected": round(t["default_projected"], 2),
            "default_ratio": round(t["default_projected"] / t["default_actual"], 4),
            "prepaid_ratio": round(t["prepaid_projected"] / t["prepaid_actual"], 4), "years": years,
            "judged_vintages": vint, "failing_default_cells": failing,
            "by_start_state": {s: round(v["default_projected"] / v["default_actual"], 4) if v["default_actual"] else None
                               for s, v in c.by_start_state.items()}}  # fmt: skip


def both(proj, start, actuals, s):
    return {
        "recorded_rule": summary(multistate.check(proj, start, actuals, s)),
        "corrected": summary(multistate.check(proj, start, actuals, s, cut_at_exit=False)),
    }


results, started = {}, time.monotonic()

# COMMAND ----------

# v2 walk-forward exams F1-F5 (the stored start tables; exam models from the training runs)
for exam in ("F1", "F2", "F3", "F4", "F5"):
    s, months = v2.EXAM_STARTS[exam]
    start = spark.sql(f"select * from {GOLD}.projection_v2_start where start_ym = '{s}'").toPandas()
    start = start.sort_values("loan_id").reset_index(drop=True)
    history = spark.sql(f"select * from {GOLD}.projection_v2_history where start_ym = '{s}'").toPandas()
    actuals = spark.sql(f"select * from {GOLD}.projection_v2_actuals where start_ym = '{s}'").toPandas()
    models = {fx.STATES.index(st): exam_model(r, exam) for st, r in v2_runs.items()}
    proj = simulate.project(models, start, history, macro, s, months, household, paths=8)
    results[f"v2_{exam}"] = both(proj, start, actuals, s)
    if exam == v2.CRISIS_EXAM:  # the v1-style benchmark the crisis exam compared against
        cols = ", ".join(["state_prev", "state", "weight", *fx.TRANSITION_FEATURES])
        bench = {}
        for st in fx.LIVE_STATES:
            f = spark.sql(f"select {cols} from {GOLD}.transitions_v2 where split = 'train' and state_prev = {st} "
                          f"and period_ym <= '{fx.V2_FOLDS[0][0]}'").toPandas()  # fmt: skip
            bench[st] = tp.fit(
                "logistic", st, f, v2.V1_BENCHMARK_SETTINGS[st], features=fx.TRANSITION_FEATURES
            )
        start_v1 = start.merge(simulate.v1_history(history), on="loan_id", how="left")
        start_v1[list(multistate.HISTORY)] = start_v1[list(multistate.HISTORY)].fillna(0.0)
        results["v2_F1_v1_benchmark"] = both(
            multistate.project(bench, start_v1, macro, s, months), start_v1, actuals, s
        )
    print(exam, {k: v["default_ratio"] for k, v in results[f"v2_{exam}"].items()})

# COMMAND ----------

# v2 holdout (final models), start built from the panel as the holdout run did
S, W = v2.HOLDOUT_START, (v2.HOLDOUT_START, v2.HOLDOUT_END)
parts = [spark.sql(fb.v2_projection_starts_sql(v, fb.databricks_relations(environment.databricks_schema, v), "spark", starts=(S,)))
         for v in range(1999, int(S[:4]) + 1)]  # fmt: skip
df = parts[0]
for p in parts[1:]:
    df = df.unionByName(p, allowMissingColumns=True)
start = df.toPandas().sort_values("loan_id").reset_index(drop=True)
history = spark.sql(fb.v2_projection_history_sql(f"{GOLD}.loan_panel", starts=(S,))).toPandas()
actuals = spark.sql(fb.v2_projection_actuals_sql(f"{GOLD}.loan_panel", windows=(W,))).toPandas()
models = {fx.STATES.index(st): pyfunc_model(r) for st, r in v2_runs.items()}
proj = simulate.project(models, start, history, macro, S, v2.HOLDOUT_MONTHS, household, paths=8)
results["v2_holdout"] = both(proj, start, actuals, S)
print("holdout", {k: v["default_ratio"] for k, v in results["v2_holdout"].items()})

# COMMAND ----------

# v1 out-of-time test (v1's locked models, slice projection), start built as notebook 60 did
WINDOW = (oot.TEST_START, oot.TEST_END)
parts = [spark.sql(fb.projection_start_sql(v, fb.databricks_relations(environment.databricks_schema, v), "spark", window=WINDOW))
         for v in range(int(fx.SPLITS[0][1][:4]), int(oot.TEST_START[:4]) + 1)]  # fmt: skip
df = parts[0]
for p in parts[1:]:
    df = df.unionByName(p)
start = df.toPandas().sort_values("loan_id").reset_index(drop=True)
actuals = spark.sql(fb.projection_actuals_sql(f"{GOLD}.loan_panel", window=WINDOW)).toPandas()
history = spark.sql(fb.projection_history_sql(f"{GOLD}.loan_panel", window=WINDOW)).toPandas()
start = start.merge(history, on="loan_id", how="left")
start[list(multistate.HISTORY)] = start[list(multistate.HISTORY)].fillna(0.0)
models = {fx.STATES.index(st): pyfunc_model(r) for st, r in v1_runs.items()}
proj = multistate.project(models, start, macro, oot.TEST_START, oot.TEST_MONTHS)
results["v1_out_of_time"] = both(proj, start, actuals, oot.TEST_START)
print("v1 out-of-time", {k: v["default_ratio"] for k, v in results["v1_out_of_time"].items()})
minutes = round((time.monotonic() - started) / 60, 1)

# COMMAND ----------

with mlflow.start_run(run_name="l039-remeasure"):
    mlflow.set_tags({"stage": "l039_remeasure", "for": "the record only (L-039); no verdict changed",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({"v2_runs": json.dumps(v2_runs), "v1_runs": json.dumps(v1_runs), "minutes": minutes})
    mlflow.log_dict(results, "l039_remeasure.json")
print(json.dumps({k: {r: v[r]["default_ratio"] for r in v} for k, v in results.items()}, indent=1))
dbutils.notebook.exit(
    json.dumps({k: {r: v[r]["default_ratio"] for r in v} for k, v in results.items()} | {"minutes": minutes})
)
