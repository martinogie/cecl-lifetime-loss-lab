# Databricks notebook source
# MAGIC %md
# MAGIC ### 57 projection check, 2017-2019 (ADR-011; ADR-009 Amendment 1 B; Phase 2 plan, multi-state, M5)
# MAGIC Scoring only, validation months only. Loans alive in January 2017 are played forward month by
# MAGIC month for 36 months with one transition model per starting state (`cecl.models.multistate`), with
# MAGIC the economy as it actually happened; projected defaults and prepayments by year and by vintage are
# MAGIC compared with what actually happened (bar 0.80-1.25 where a cell has at least 200 actual events).
# MAGIC
# MAGIC Parameter `combinations`: JSON {label: {"current": run_id, "30": run_id, "60": run_id}}, for
# MAGIC example the chosen combination and the all-logistic benchmark; each is projected and checked, and
# MAGIC the cells the chosen one fails that the benchmark passes are listed (the combination check).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("combinations", "{}"), ("benchmark", "all_logistic"),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import multistate
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
combinations = json.loads(dbutils.widgets.get("combinations"))
benchmark = dbutils.widgets.get("benchmark")
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
START, END = fb.PROJECTION_START[fx.VALIDATE]
MONTHS = multistate.months_between(START, END)

# COMMAND ----------

started = time.monotonic()
start = (
    spark.table(f"{GOLD}.projection_start_validate").toPandas().sort_values("loan_id").reset_index(drop=True)
)
actuals = spark.table(f"{GOLD}.projection_actuals_validate").toPandas()
# each loan's actual late-payment history before the start month (its 12 panel rows), so the projection
# drops start months out of the 12-month window exactly as the data does (limitations log L-026)
history = spark.sql(fb.projection_history_sql(f"{GOLD}.loan_panel", fx.VALIDATE)).toPandas()
start = start.merge(history, on="loan_id", how="left")
start[list(multistate.HISTORY)] = start[list(multistate.HISTORY)].fillna(0.0)
mismatched = multistate.check_history(start)
print(
    f"history tie-out: {len(start) - mismatched:,} of {len(start):,} loans' 12 flags add up to late_months_12"
)
if mismatched:
    raise ValueError(f"{mismatched:,} loans' start history does not match late_months_12: stopping")
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
print(f"loans alive in {START}: {len(start):,}; projecting {MONTHS} months")
checks, timings = {}, {}
for label, runs in combinations.items():
    t = time.monotonic()
    models = {fx.STATES.index(s): mlflow.pyfunc.load_model(f"runs:/{r}/model").unwrap_python_model().inner.model
              for s, r in runs.items()}  # fmt: skip
    projection = multistate.project(models, start, macro, START, MONTHS)
    checks[label] = multistate.check(projection, start, actuals, START)
    timings[label] = round((time.monotonic() - t) / 60, 2)
    c = checks[label]
    print(
        label, f"passes: {c.passes};", {k: round(v) for k, v in c.totals.items()}, f"({timings[label]} min)"
    )
newly = {}
if benchmark in checks:
    for label, c in checks.items():
        if label != benchmark:
            newly[label] = [vars(x) for x in multistate.newly_failing(c, checks[benchmark])]
minutes = round((time.monotonic() - started) / 60, 1)
out = {"start": START, "end": END, "loans": len(start), "history_tie_out_mismatches": mismatched, "minutes": minutes, "minutes_by_combination": timings,
       "combinations": combinations, "checks": {k: v.as_dict() for k, v in checks.items()},
       "newly_failing_vs_benchmark": newly}  # fmt: skip
with mlflow.start_run(run_name="projection-check-2017-2019"):
    mlflow.set_tags({"stage": "projection_check", "phase": "2", "design": "multi-state (ADR-011)",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params(
        {"start": START, "end": END, "minutes": minutes, "combinations": json.dumps(combinations)[:500]}
    )
    for label, c in checks.items():
        mlflow.log_metric(f"{label}_passes", float(c.passes))
        for k, v in c.totals.items():
            mlflow.log_metric(f"{label}_{k}", v)
    mlflow.log_dict(out, "projection_check.json")
print(json.dumps({k: v for k, v in out.items() if k != "checks"}, indent=1, default=str))
dbutils.notebook.exit(json.dumps({"minutes": minutes, "passes": {k: v.passes for k, v in checks.items()},
                                  "totals": {k: v.totals for k, v in checks.items()},
                                  "by_start_state": {k: v.by_start_state for k, v in checks.items()},
                                  "newly_failing": newly}))  # fmt: skip
