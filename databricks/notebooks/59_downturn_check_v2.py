# Databricks notebook source
# MAGIC %md
# MAGIC ### 59 downturn-sensitivity check, model v2 (ADR-009 Amendment 4)
# MAGIC The final v2 fit (one run per step, from the selection) plays every loan alive on 1 July 2022 forward
# MAGIC 36 months with the path simulation (8 paths, the same seed in every scenario): once with the economy held
# MAGIC at its June 2022 level (baseline), then under each shock (house prices -10/-20/-30%, unemployment +2/+4
# MAGIC points, and -30% with +4 together; each built over 12 months, then held). Thin wrapper around
# MAGIC `cecl.models.sensitivity`, whose tests 1-7 were approved before this code was written.
# MAGIC The 1 July 2022 book is inside the training window; no holdout row is read.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("runs", "{}"), ("paths", "8"),
                      ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow

from cecl import environment
from cecl import features as fx
from cecl.models import multistate, simulate
from cecl.models import sensitivity as sv

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
runs = json.loads(dbutils.widgets.get("runs"))  # {"current": run_id, "30": run_id, "60": run_id}
paths = int(dbutils.widgets.get("paths"))
if sorted(runs) != sorted(fx.STATES[s] for s in fx.LIVE_STATES):
    raise ValueError(f"runs must name one final v2 run per step, got {runs}")
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))


def final_model(run_id: str):
    """The final v2 fit inside the run's pyfunc (pyfunc -> adapter -> TransitionPyfunc -> model)."""
    py = mlflow.pyfunc.load_model(f"runs:/{run_id}/model").unwrap_python_model()
    return py.inner.model


models = {fx.STATES.index(s): final_model(r) for s, r in runs.items()}
for s, m in models.items():
    if tuple(getattr(m, "features", ())) != fx.V2_BASE_FEATURES:
        raise ValueError(f"step {fx.STATES[s]}: not a v2 model with the 28 base inputs")
actual = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                      spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
start = spark.sql(f"select * from {GOLD}.projection_v2_start where start_ym = '{sv.START_YM}'").toPandas()
start = start.sort_values("loan_id").reset_index(drop=True)
history = spark.sql(f"select * from {GOLD}.projection_v2_history where start_ym = '{sv.START_YM}'").toPandas()
history_check = simulate.check_history(start, history)
print(f"book on {sv.START_YM}: {len(start):,} loans; history check {history_check}")

# COMMAND ----------

started = time.monotonic()
scen = {sh.name: sv.scenario(actual, sh) for sh in sv.ALL}
tie = {sh.name: sv.tie_out(start, scen["baseline"], scen[sh.name], sh) for sh in sv.SHOCKS}
defaults, ltv, minutes = {}, {}, {}
for sh in sv.ALL:
    t = time.monotonic()
    proj = simulate.project(models, start, history, scen[sh.name], sv.START_YM, sv.MONTHS, None, paths=paths)
    defaults[sh.name] = proj.p_default.sum(axis=1)
    if sh.name in sv.ORDERING_SHOCKS:
        ltv[sh.name] = sv.ltv_after(start, scen[sh.name])
    minutes[sh.name] = round((time.monotonic() - t) / 60, 2)
    print(f"{sh.name}: {defaults[sh.name].sum():,.0f} projected defaults ({minutes[sh.name]} min)")

r = sv.ratios(defaults, sv.groups(start, ltv))
verdict = sv.evaluate(r, {"history_check": history_check, "tie_out": tie})
total_minutes = round((time.monotonic() - started) / 60, 1)
out = {"rules": "ADR-009 Amendment 4", "start": sv.START_YM, "months": sv.MONTHS, "paths": paths, "loans": len(start),
       "runs": runs, "verdict": verdict, "ratios": r, "minutes": total_minutes, "minutes_by_scenario": minutes}  # fmt: skip

# COMMAND ----------

with mlflow.start_run(run_name="v2-downturn-check"):
    mlflow.set_tags({"model_version": "v2", "stage": "downturn_check", "rules": "ADR-009 Amendment 4",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({"runs": json.dumps(runs), "paths": paths, "start": sv.START_YM, "months": sv.MONTHS})
    mlflow.log_metric("passes", float(verdict["passes"]))
    for s, v in r["R"].items():
        mlflow.log_metric(f"R_{s}".replace("-", "m").replace("+", "p"), v)
    mlflow.log_dict(out, "downturn_check.json")
print(json.dumps({"R": r["R"], "passes": verdict["passes"],
                  "tests": {k: t["passes"] for k, t in verdict["tests"].items()}}, indent=1))  # fmt: skip
dbutils.notebook.exit(json.dumps({"passes": verdict["passes"], "R": r["R"],
                                  "tests": {k: t["passes"] for k, t in verdict["tests"].items()},
                                  "minutes": total_minutes}, default=str))  # fmt: skip
