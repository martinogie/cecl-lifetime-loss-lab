# Databricks notebook source
# MAGIC %md
# MAGIC ### 61 the model v2 holdout, once (ADR-009 Amendment 3 E; locked by Amendment 5)
# MAGIC Scores July 2024 - June 2026 for **v2 @challenger** (the final v2 fits, logistic at all three steps).
# MAGIC Started only by `scripts/run_v2_holdout.py`, which checks ADR-009 (Amendment 5 and its lock), Martins's
# MAGIC final written expectation, the @challenger registration and the downturn check first.
# MAGIC
# MAGIC 1. The **full projection** (path simulation, 8 paths, fixed seed) from every loan's state on 1 July 2024
# MAGIC    for 24 months, the economy as it actually happened; the start table, its history and the outcomes are
# MAGIC    built here, at run time, from the panel (no holdout start table exists before this runs).
# MAGIC 2. The **verdict** (Amendment 3 E): every judged default cell (by year and vintage, 200+ actual defaults)
# MAGIC    and the total within 0.80-1.25; payoff cells reported (L-028).
# MAGIC 3. **Each transition's calibration**, log loss, AUC and Gini on the holdout months (v2; v1's retired
# MAGIC    models alongside for information).
# MAGIC
# MAGIC **Once:** if any `v2_holdout` run already exists in the experiment, this stops before scoring anything.
# MAGIC The result stands whatever it shows; nothing is chosen, swapped or tuned on it.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("runs", "{}"), ("v1_runs", "{}"), ("paths", "8"),
                      ("adr_009_commit", ""), ("expectations_commit", ""), ("downturn_runs", ""),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
from pyspark.sql import functions as F

from cecl import environment
from cecl import features as fx
from cecl.models import multistate, oot, simulate
from cecl.models import transition_pipeline as tp
from cecl.models import v2_pipeline as v2
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
runs = json.loads(dbutils.widgets.get("runs"))  # v2 @challenger {step: run_id}
v1_runs = json.loads(dbutils.widgets.get("v1_runs"))  # v1's retired models, information only
paths = int(dbutils.widgets.get("paths"))
adr_commit, exp_commit = dbutils.widgets.get("adr_009_commit"), dbutils.widgets.get("expectations_commit")
if sorted(runs) != sorted(oot.STEPS) or not adr_commit or not exp_commit:
    raise ValueError(
        "the holdout needs the v2 runs, the ADR-009 and expectations commits (scripts/run_v2_holdout.py)"
    )
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
experiment = mlflow.set_experiment(environment.mlflow_experiment_path(user))
earlier = mlflow.search_runs([experiment.experiment_id], "tags.stage = 'v2_holdout'", output_format="list")
if earlier:
    raise RuntimeError(
        f"the v2 holdout has already run ({earlier[0].info.run_id}); it runs once (Amendment 3 E)"
    )
S, MONTHS = v2.HOLDOUT_START, v2.HOLDOUT_MONTHS
WINDOW = (S, v2.HOLDOUT_END)
print(f"v2 runs {runs}; holdout {WINDOW}, {MONTHS} months, {paths} paths")

# COMMAND ----------

# 1. the start table on 1 July 2024, its 12-month history and the outcomes, built here from the panel
started = time.monotonic()
parts = []
for vintage in range(1999, int(S[:4]) + 1):
    rel = fb.databricks_relations(environment.databricks_schema, vintage)
    parts.append(spark.sql(fb.v2_projection_starts_sql(vintage, rel, "spark", starts=(S,))))
start_df = parts[0]
for part in parts[1:]:
    start_df = start_df.unionByName(part, allowMissingColumns=True)
start = start_df.toPandas().sort_values("loan_id").reset_index(drop=True)
panel = f"{GOLD}.loan_panel"
history = spark.sql(fb.v2_projection_history_sql(panel, starts=(S,))).toPandas()
actuals = spark.sql(fb.v2_projection_actuals_sql(panel, windows=(WINDOW,))).toPandas()
history_check = simulate.check_history(start, history)
if any(history_check.values()):
    raise ValueError(f"start inputs differ from the rebuilt history {history_check}: stopping")
no_outcome, not_alive = oot.start_actuals_tie_out(start["loan_id"], actuals["loan_id"])
if no_outcome:
    raise ValueError(f"tie-out: {no_outcome:,} start loans have no outcome row: stopping")
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
household = simulate.Household.from_table(spark.table(f"{SILVER}.macro_household_monthly").toPandas())
print(
    f"loans alive in {S}: {len(start):,}; history and outcomes tie out ({not_alive:,} censored in {S}, not alive)"
)


def load(run_id):
    return mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model().inner.model


models = {fx.STATES.index(s): load(r) for s, r in runs.items()}
t = time.monotonic()
projection = simulate.project(models, start, history, macro, S, MONTHS, household, paths=paths)
check = multistate.check(projection, start, actuals, S)
result = v2.holdout_verdict(check)
projection_minutes = round((time.monotonic() - t) / 60, 2)
print({k: round(v) for k, v in check.totals.items()}, f"({projection_minutes} min)")
print("VERDICT:", "PASS" if result["passes"] else "FAIL", f"total {result['total_ratio']:.3f};",
      f"{len(result['failing_default_cells'])} default cells outside the bar")  # fmt: skip

# COMMAND ----------

# 3. each transition on the holdout months (v2; v1's retired models for information)
v2_cols = list(
    dict.fromkeys(["loan_id", "vintage_year", "period_ym", "state_prev", "state", "weight", *fx.V2_STORED])
)
holdout = spark.sql(f"select {', '.join(v2_cols)} from {GOLD}.transitions_v2_holdout").toPandas()
holdout = v2.add_derived(holdout, fx.V2_BASE_FEATURES)
baseline = (
    spark.table(f"{GOLD}.transitions_v2").where(F.col("split") == fx.V2_TRAIN).select(*v2_cols)
    .sample(0.02, seed=7).toPandas()
)  # fmt: skip
baseline = v2.add_derived(baseline, fx.V2_BASE_FEATURES)
transitions = {}
for step in oot.STEPS:
    s = fx.STATES.index(step)
    frame, train_frame = tp.starting_in(holdout, s), tp.starting_in(baseline, s)
    transitions[f"{step}|v2"] = tp.evaluate(models[s], frame, train_frame, "2024-2026")
    if step in v1_runs:  # information only: v1's locked model on the same months
        transitions[f"{step}|v1_info"] = tp.evaluate(load(v1_runs[step]), frame, train_frame, "2024-2026")
minutes = round((time.monotonic() - started) / 60, 1)

# COMMAND ----------

with mlflow.start_run(run_name="v2-holdout") as run:
    mlflow.set_tags({"stage": "v2_holdout", "model_version": "v2", "rules": "ADR-009 Amendment 3 E (locked, Amendment 5)",
                     "adr_009_commit": adr_commit, "expectations_commit": exp_commit,
                     "downturn_runs": dbutils.widgets.get("downturn_runs"),
                     "verdict": "pass" if result["passes"] else "fail",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({f"run_{k}": v for k, v in runs.items()}
                      | {"holdout_months": f"{S}-{v2.HOLDOUT_END}", "loans": len(start), "paths": paths, "minutes": minutes})  # fmt: skip
    mlflow.log_metric("passes", float(result["passes"]))
    mlflow.log_metric("total_default_ratio", result["total_ratio"])
    for k, v in check.totals.items():
        mlflow.log_metric(k, v)
    for key, r in transitions.items():
        mlflow.log_metrics(tp.scalar_metrics(r, f"holdout_{key.replace('|', '_')}"))
    mlflow.log_dict({"verdict": result, "check": check.as_dict(), "history_check": history_check,
                     "projection_minutes": projection_minutes}, "holdout_projection.json")  # fmt: skip
    mlflow.log_dict(transitions, "holdout_transitions.json")
    holdout_run = run.info.run_id

summary = {"holdout_run": holdout_run, "verdict": "pass" if result["passes"] else "fail", "minutes": minutes,
           "total_ratio": result["total_ratio"], "failing_default_cells": result["failing_default_cells"],
           "payoff_cells_failing": len(result["failing_payoff_cells_recorded_under_L028"]),
           "totals": check.totals, "by_start_state": check.by_start_state}  # fmt: skip
print(json.dumps(summary, indent=1, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
