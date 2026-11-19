# Databricks notebook source
# MAGIC %md
# MAGIC ### 60 the out-of-time test, once (ADR-009 E; Amendments 1 E and 2; multi-state, ADR-011)
# MAGIC Scores July 2022 - June 2026 for the **locked combination** (one transition model per starting
# MAGIC state, logged as the newest multi-state `selection` run and registered as @challenger) and, for
# MAGIC information only, other models and combinations. Started only by `scripts/run_oot_test.py`, which
# MAGIC checks ADR-009, Martins's written expectation, the pre-registered result statement, the
# MAGIC @challenger registration and the COVID sensitivity first.
# MAGIC
# MAGIC 1. The **full multi-state projection** of each combination from every loan's state on 1 July 2022
# MAGIC    to June 2026 (the start table is built here, at run time, from the panel: no test-month table
# MAGIC    exists before this runs), projected vs actual defaults and prepayments by year and vintage.
# MAGIC 2. The **verdict** (Amendment 2 (a), (b)), on the locked combination only: every judged default cell
# MAGIC    inside 0.80-1.25; payoff failures recorded under L-028 (`cecl.models.oot.verdict`).
# MAGIC 3. **Each transition's calibration**, log loss, AUC and Gini on the test months (holdout table).
# MAGIC
# MAGIC **Once:** if any `out_of_time_test` run already exists in the experiment, this stops without
# MAGIC scoring anything. The results are recorded whatever they show; no model is chosen, swapped or
# MAGIC tuned on them.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("locked", "{}"), ("info_combinations", "{}"),
                      ("info_models", "{}"), ("selection_run", ""), ("adr_009_commit", ""),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
from pyspark.sql import functions as F

from cecl import environment
from cecl import features as fx
from cecl.models import multistate, oot, transition_pipeline
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
locked = oot.locked_set({f"run_{k}": v for k, v in json.loads(dbutils.widgets.get("locked")).items()})
info_combinations = json.loads(dbutils.widgets.get("info_combinations"))  # {label: {step: run_id}}
info_models = json.loads(dbutils.widgets.get("info_models"))  # {step: {family: run_id}}
adr_commit, selection_run = dbutils.widgets.get("adr_009_commit"), dbutils.widgets.get("selection_run")
if not adr_commit or not selection_run:
    raise ValueError("the test needs the ADR-009 commit and the selection run (scripts/run_oot_test.py)")
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
WINDOW = (oot.TEST_START, oot.TEST_END)
print(f"locked: {locked}; test window {WINDOW}, {oot.TEST_MONTHS} months")

# COMMAND ----------

# 1. the start table on 1 July 2022, built here from the panel (every loan alive that month)
started = time.monotonic()
parts = []
for vintage in range(int(fx.SPLITS[0][1][:4]), int(oot.TEST_START[:4]) + 1):
    rel = fb.databricks_relations(environment.databricks_schema, vintage)
    parts.append(spark.sql(fb.projection_start_sql(vintage, rel, "spark", window=WINDOW)))
start_df = parts[0]
for part in parts[1:]:
    start_df = start_df.unionByName(part)
start = start_df.toPandas().sort_values("loan_id").reset_index(drop=True)
actuals = spark.sql(fb.projection_actuals_sql(f"{GOLD}.loan_panel", window=WINDOW)).toPandas()
history = spark.sql(fb.projection_history_sql(f"{GOLD}.loan_panel", window=WINDOW)).toPandas()
start = start.merge(history, on="loan_id", how="left")
start[list(multistate.HISTORY)] = start[list(multistate.HISTORY)].fillna(0.0)
mismatched = multistate.check_history(start)
if mismatched:
    raise ValueError(f"{mismatched:,} loans' start history does not match late_months_12: stopping")
no_outcome, not_alive = oot.start_actuals_tie_out(start["loan_id"], actuals["loan_id"])
if no_outcome:
    raise ValueError(f"tie-out: {no_outcome:,} start loans have no outcome row: stopping")
print(f"{not_alive:,} loans with a censored {oot.TEST_START} row are not in the start table (not alive)")
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
print(f"loans alive in {oot.TEST_START}: {len(start):,}; history and actuals tie out")


def load(run_id):
    return mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model


checks, timings = {}, {}
for label, runs in {"locked": locked, **info_combinations}.items():
    t = time.monotonic()
    models = {fx.STATES.index(s): load(r) for s, r in runs.items()}
    projection = multistate.project(models, start, macro, oot.TEST_START, oot.TEST_MONTHS)
    checks[label] = multistate.check(projection, start, actuals, oot.TEST_START)
    timings[label] = round((time.monotonic() - t) / 60, 2)
    print(label, {k: round(v) for k, v in checks[label].totals.items()}, f"({timings[label]} min)")
result = oot.verdict(checks["locked"])
print(
    "VERDICT:",
    "PASS" if result["passes"] else "FAIL",
    f"({len(result['failing_default_cells'])} default cells outside the bar)",
)

# COMMAND ----------

# 3. each transition's calibration, log loss, AUC and Gini on the test months (holdout table)
cols = ", ".join(
    ["loan_id", "vintage_year", "period_ym", "state_prev", "state", "weight", *fx.TRANSITION_FEATURES]
)
holdout = spark.sql(f"select {cols} from {GOLD}.transitions_holdout").toPandas()
baseline = (
    spark.table(f"{GOLD}.transitions").where(F.col("split") == fx.TRAIN).select(*cols.split(", "))
    .sample(0.02, seed=7).toPandas()
)  # fmt: skip
transitions = {}
for step in oot.STEPS:
    s = fx.STATES.index(step)
    frame, train_frame = (
        transition_pipeline.starting_in(holdout, s),
        transition_pipeline.starting_in(baseline, s),
    )
    for family, run_id in {"locked": locked[step], **info_models.get(step, {})}.items():
        transitions[f"{step}|{family}"] = transition_pipeline.evaluate(
            load(run_id), frame, train_frame, "2022-2026"
        )
minutes = round((time.monotonic() - started) / 60, 1)

# COMMAND ----------

with mlflow.start_run(run_name="out-of-time-test") as run:
    mlflow.set_tags({"stage": "out_of_time_test", "design": "multi-state (ADR-011)", "adr_009_commit": adr_commit,
                     "selection_run": selection_run, "verdict": "pass" if result["passes"] else "fail", "phase": "2",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({f"run_{k}": v for k, v in locked.items()}
                      | {"test_months": f"{oot.TEST_START}-{oot.TEST_END}", "loans": len(start), "minutes": minutes})  # fmt: skip
    mlflow.log_metric("passes", float(result["passes"]))
    for label, c in checks.items():
        for k, v in c.totals.items():
            mlflow.log_metric(f"{label}_{k}", v)
    for key, r in transitions.items():
        mlflow.log_metrics(transition_pipeline.scalar_metrics(r, f"test_{key.replace('|', '_')}"))
    mlflow.log_dict({"verdict": result, "checks": {k: v.as_dict() for k, v in checks.items()},
                     "minutes_by_combination": timings}, "out_of_time_projection.json")  # fmt: skip
    mlflow.log_dict(transitions, "out_of_time_transitions.json")
    test_run = run.info.run_id

summary = {"test_run": test_run, "verdict": "pass" if result["passes"] else "fail", "minutes": minutes,
           "failing_default_cells": result["failing_default_cells"],
           "payoff_cells_failing": len(result["failing_payoff_cells_recorded_under_L028"]),
           "totals": {k: v.totals for k, v in checks.items()},
           "by_start_state": {k: v.by_start_state for k, v in checks.items()}}  # fmt: skip
print(json.dumps(summary, indent=1, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
