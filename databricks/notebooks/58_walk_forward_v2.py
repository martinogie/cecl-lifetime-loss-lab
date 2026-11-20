# Databricks notebook source
# MAGIC %md
# MAGIC ### 58 walk-forward exams, model v2 (ADR-009 Amendment 3)
# MAGIC For each exam, the models fitted on that exam's training months (kept by the training runs) play every
# MAGIC loan alive at the exam's start forward month by month with the **path simulation** (`cecl.models.simulate`),
# MAGIC the economy as it actually happened, and the projection is compared with what happened:
# MAGIC - F2 (2011-2013), F3 (2014-2016), F4 (2017-2019): total projected defaults and every year within 0.80-1.25;
# MAGIC - F1 (2008-2010, the crisis): v2 passes if its total is closer to actual than the v1-style benchmark
# MAGIC   (v1's inputs and logistic settings, refitted on the same exam's training months, projected by v1's method);
# MAGIC - F5 (July 2022 - June 2024): reported only.
# MAGIC The chosen combination and the all-logistic combination are both projected; the combination check of
# MAGIC Amendment 1 swaps steps back to logistic if the chosen combination fails something all-logistic passes.
# MAGIC A simulation-noise check (two seeds on F4, all-logistic) is recorded. The holdout months are never read.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import pickle
import time

for name, default in (("env", ""), ("layout", "catalogs"), ("chosen", "{}"), ("logistic", "{}"), ("paths", "8"),
                      ("exams", "F1,F2,F3,F4,F5"), ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
import numpy as np

from cecl import environment
from cecl import features as fx
from cecl.models import multistate, simulate
from cecl.models import transition_pipeline as tp
from cecl.models import v2_pipeline as v2

environment.require_airflow_env()
GOLD, SILVER = environment.databricks_schema("gold"), environment.databricks_schema("silver")
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
chosen = json.loads(dbutils.widgets.get("chosen"))  # {"current": run_id, "30": run_id, "60": run_id}
logistic = json.loads(dbutils.widgets.get("logistic"))
paths = int(dbutils.widgets.get("paths"))
exams = [e.strip() for e in dbutils.widgets.get("exams").split(",") if e.strip()]
user = spark.sql("select current_user()").collect()[0][0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(environment.mlflow_experiment_path(user))
macro = multistate.Macro.from_tables(spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
                                     spark.table(f"{SILVER}.macro_national_monthly").toPandas())  # fmt: skip
household = simulate.Household.from_table(spark.table(f"{SILVER}.macro_household_monthly").toPandas())
_cache: dict = {}


def exam_model(run_id: str, exam: str):
    key = (run_id, exam)
    if key not in _cache:
        name = "F5_info" if exam == "F5" else exam
        path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=f"exam_models/{name}.pkl")
        with open(path, "rb") as fh:
            _cache[key] = pickle.load(fh)
    return _cache[key]


def combo(runs: dict, exam: str) -> dict:
    return {fx.STATES.index(s): exam_model(r, exam) for s, r in runs.items()}


def tables(exam: str):
    s = v2.EXAM_STARTS[exam][0]
    start = spark.sql(f"select * from {GOLD}.projection_v2_start where start_ym = '{s}'").toPandas()
    start = start.sort_values("loan_id").reset_index(drop=True)
    history = spark.sql(f"select * from {GOLD}.projection_v2_history where start_ym = '{s}'").toPandas()
    actuals = spark.sql(f"select * from {GOLD}.projection_v2_actuals where start_ym = '{s}'").toPandas()
    return start, history, actuals


def v1_benchmark(exam: str) -> dict:
    """v1's inputs and locked logistic settings, refitted on this exam's training months."""
    last_train = fx.V2_FOLDS[int(exam[1]) - 1][0] if exam != "F5" else fx.V2_INFO_FOLD[0]
    cols = ", ".join(["state_prev", "state", "weight", *fx.TRANSITION_FEATURES])
    out = {}
    for s in fx.LIVE_STATES:
        f = spark.sql(f"select {cols} from {GOLD}.transitions_v2 where split = 'train' and state_prev = {s} "
                      f"and period_ym <= '{last_train}'").toPandas()  # fmt: skip
        out[s] = tp.fit("logistic", s, f, v2.V1_BENCHMARK_SETTINGS[s], features=fx.TRANSITION_FEATURES)
    return out


# COMMAND ----------

started = time.monotonic()
results, timings, history_checks = {}, {}, {}
for exam in exams:
    s, months = v2.EXAM_STARTS[exam]
    start, history, actuals = tables(exam)
    history_checks[exam] = simulate.check_history(start, history)
    if any(history_checks[exam].values()):
        raise ValueError(
            f"{exam}: start inputs differ from the rebuilt history {history_checks[exam]}: stopping"
        )
    row = {"start": s, "months": months, "loans": len(start), "projections": {}}
    for label, runs in (("chosen", chosen), ("all_logistic", logistic)):
        if label == "all_logistic" and runs == chosen:
            row["projections"][label] = row["projections"]["chosen"]
            continue
        t = time.monotonic()
        proj = simulate.project(combo(runs, exam), start, history, macro, s, months, household, paths=paths)
        c = multistate.check(proj, start, actuals, s)
        row["projections"][label] = {"check": c.as_dict(), "verdict": v2.exam_verdict(c), "_check": c}
        timings[f"{exam} {label}"] = round((time.monotonic() - t) / 60, 2)
        if (
            exam == "F4" and label == "chosen"
        ):  # kept for the combined expected-loss check (Phase 3 LGD x EAD)
            import pandas as pd

            frame = pd.DataFrame(proj.p_default, columns=proj.months)
            frame.insert(0, "loan_id", start["loan_id"].to_numpy())
            os.makedirs(f"{VOLUME}/results/v2", exist_ok=True)
            frame.to_parquet(f"{VOLUME}/results/v2/F4_p_default_chosen.parquet", index=False)
        print(exam, label, round(v2.default_ratio(c), 3), f"({timings[f'{exam} {label}']} min)")
    if exam == v2.CRISIS_EXAM:
        t = time.monotonic()
        bench = v1_benchmark(exam)
        hist_v1 = simulate.v1_history(history)
        start_v1 = start.merge(hist_v1, on="loan_id", how="left")
        start_v1[list(multistate.HISTORY)] = start_v1[list(multistate.HISTORY)].fillna(0.0)
        bc = multistate.check(multistate.project(bench, start_v1, macro, s, months), start_v1, actuals, s)
        row["benchmark_v1"] = {"check": bc.as_dict(), "_check": bc}
        for label in ("chosen", "all_logistic"):
            row["projections"][label]["verdict"] = v2.crisis_verdict(row["projections"][label]["_check"], bc)
        timings[f"{exam} benchmark"] = round((time.monotonic() - t) / 60, 2)
        print(exam, "v1 benchmark", round(v2.default_ratio(bc), 3))
    results[exam] = row

# Simulation noise: all-logistic F4 again with another seed (total defaults, relative difference)
noise = None
if "F4" in exams:
    start, history, actuals = tables("F4")
    again = simulate.project(
        combo(logistic, "F4"), start, history, macro, "201701", 36, household, paths=paths, seed=8
    )
    c2 = multistate.check(again, start, actuals, "201701")
    first = results["F4"]["projections"]["all_logistic"]["_check"]
    noise = abs(c2.totals["default_projected"] / first.totals["default_projected"] - 1.0)
    print(f"simulation noise on F4 total defaults (two seeds, {paths} paths): {noise:.2%}")

# COMMAND ----------


def judged(label: str) -> dict:
    out = {}
    for exam in exams:
        if exam in v2.INFO_EXAMS:
            continue
        out[exam] = results[exam]["projections"][label]["verdict"]["passes"]
    return out


verdict = {"chosen": judged("chosen"), "all_logistic": judged("all_logistic")}
# Combination check (Amendment 1, kept by Amendment 3): an exam the chosen combination fails and all-logistic passes
newly = [e for e, ok in verdict["chosen"].items() if not ok and verdict["all_logistic"][e]]
passes = all(verdict["chosen"].values())
minutes = round((time.monotonic() - started) / 60, 1)
for exam in results:
    for p in results[exam]["projections"].values():
        p.pop("_check", None)
    results[exam].get("benchmark_v1", {}).pop("_check", None)
out = {"exams": results, "verdict": verdict, "chosen_passes_walk_forward": passes,
       "combination_check_needed_for": newly, "simulation_noise_F4": noise, "paths": paths,
       "history_checks": history_checks, "chosen": chosen, "logistic": logistic, "minutes": minutes,
       "minutes_by_projection": timings}  # fmt: skip
with mlflow.start_run(run_name="v2-walk-forward"):
    mlflow.set_tags({"model_version": "v2", "stage": "walk_forward", "rules": "ADR-009 Amendment 3",
                     "cecl_env": os.environ["CECL_ENV"], "git_commit": dbutils.widgets.get("code_version")})  # fmt: skip
    mlflow.log_params({"chosen": json.dumps(chosen), "logistic": json.dumps(logistic), "paths": paths,
                       "exams": ",".join(exams), "minutes": minutes})  # fmt: skip
    mlflow.log_metric("chosen_passes_walk_forward", float(passes))
    for exam in results:
        for label, p in results[exam]["projections"].items():
            mlflow.log_metric(
                f"{exam}_{label}_default_ratio",
                p["verdict"].get("total_ratio", p["verdict"].get("v2_ratio", np.nan)),
            )
    mlflow.log_dict(out, "walk_forward.json")
print(json.dumps(out["verdict"], indent=1), json.dumps(timings, indent=1))
dbutils.notebook.exit(
    json.dumps(
        {"passes": passes, "verdict": verdict, "newly_failing": newly, "noise": noise, "minutes": minutes},
        default=str,
    )
)
