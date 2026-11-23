# Databricks notebook source
# MAGIC %md
# MAGIC ### 69 Model monitoring (Phase 8): weekly score stability, quarterly calibration
# MAGIC The rules are in `cecl/monitoring.py` (roadmap v2 changes, approved 18 November 2026). The models are the
# MAGIC registry's v2 transition models at `@challenger` (no `@champion` exists: v2 failed its holdout, L-038).
# MAGIC
# MAGIC **weekly** (`mode=weekly`; the DAG starts it only when a new economic month has arrived): the book alive at
# MAGIC the end of Freddie's latest month, carried forward along the v2 path simulation to the latest economic month,
# MAGIC scored one month ahead; for each live state (current, 30, 60) the chance of default and of payoff are
# MAGIC compared with the baseline profile by PSI. The baseline is the book at `baseline_ym` (30 June 2026, the
# MAGIC reserve the model first served), scored one month ahead, saved once as a profile (deciles and shares only).
# MAGIC
# MAGIC **quarterly** (`mode=quarterly`, `reserve_ym=R`; run by the close): for each month of the quarter ending R,
# MAGIC every loan's actual state at the start of the month, scored one month ahead (actual economy, no simulation),
# MAGIC against what happened: predicted / actual per transition by year, origination year and starting state
# MAGIC (ADR-009 bar: 0.80-1.25 where a cell has at least 200 events), AUC, one trend point, the retrain check.
# MAGIC
# MAGIC Outputs (private volume, aggregates only): `monitoring/baseline.json`, `monitoring/weekly/<date>.json`,
# MAGIC `monitoring/quarterly/<R>.json` and `monitoring/trend.json`.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
from datetime import date, datetime

for name, default in (("env", ""), ("layout", "catalogs"), ("mode", "weekly"), ("reserve_ym", ""),
                      ("baseline_ym", "202606"), ("trained_on", "2026-11-18"), ("code_version", "unknown")):  # fmt: skip
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
import numpy as np

from cecl import close, environment, metrics, monitoring, scenarios
from cecl import features as fx
from cecl.models import multistate, simulate
from ingestion import reserve_book as rb

environment.require_airflow_env()
SILVER, GOLD = environment.databricks_schema("silver"), environment.databricks_schema("gold")
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
OUT = f"{VOLUME}/monitoring"
MODE = dbutils.widgets.get("mode").strip()
if MODE not in ("weekly", "quarterly"):
    raise ValueError(f"mode must be weekly or quarterly, got {MODE!r}")
os.makedirs(f"{OUT}/weekly", exist_ok=True)
os.makedirs(f"{OUT}/quarterly", exist_ok=True)
STEPS = ("current", "30", "60")
mlflow.set_registry_uri("databricks-uc")
models, versions = {}, {}
client = mlflow.MlflowClient()
for step in STEPS:
    name = environment.registered_model(f"pd_v2_transition_{step}")
    v = client.get_model_version_by_alias(name, environment.CHALLENGER)
    # Loaded from the version's training run, as the reserve projection does: serverless compute may not read
    # model files from Unity Catalog storage here (found in the Phase 8 dev check, 23 Nov 2026).
    models[fx.STATES.index(step)] = (
        mlflow.pyfunc.load_model(f"runs:/{v.run_id}/model").unwrap_python_model().inner.model
    )
    versions[name] = str(v.version)
state_m = spark.table(f"{SILVER}.macro_state_monthly").toPandas()
nat_m = spark.table(f"{SILVER}.macro_national_monthly").toPandas()
long_run = spark.table(f"{SILVER}.macro_long_run_averages").toPandas()
fed = {y: spark.table(f"{SILVER}.macro_{t}").toPandas() for y, t in scenarios.FED_TABLES.items()}


def scores(m0, economy=None, paths=1):
    """Each live loan-path's one-month-ahead transition chances, by state: the book at the end of m0 played
    forward to the economy month (m0 itself when None) and scored for the month after it."""
    start, history = rb.reserve_book(spark, m0)
    E = economy or m0
    k = multistate.months_between(m0, E)  # the projected month whose inputs use the economy month E
    weights = start.groupby("property_state")["upb_start"].sum().to_dict()
    sc = scenarios.build(
        "baseline", E, state_m, nat_m, fed[scenarios.fed_year(E)], long_run, weights, horizon=3
    )
    got = {}

    def observe(step, state, alive, hist, loan, probs):
        if step == k:
            for s, P in probs.items():
                got[s] = (loan[np.flatnonzero(alive & (state == s))], P)

    simulate.project(models, start, history, sc.macro, m0, k + 1, None, paths=paths,
                     first_state=start["first_state"].to_numpy(), first_pause=start["first_pause"].to_numpy(),
                     observer=observe)  # fmt: skip
    return start, got


def transition_scores(got):
    """{"current->default": array, "current->prepaid": array, ...} for the PSI."""
    out = {}
    for s, (_, P) in got.items():
        out[f"{fx.STATES[s]}->default"] = P[:, fx.DEFAULTED]
        out[f"{fx.STATES[s]}->prepaid"] = P[:, fx.PREPAID]
    return out


# COMMAND ----------

if MODE == "weekly":
    base_path = f"{OUT}/baseline.json"
    if not os.path.exists(base_path):
        b_ym = dbutils.widgets.get("baseline_ym").strip()
        _, got = scores(b_ym)
        baseline = {"baseline_ym": b_ym, "models": versions,
                    "profiles": {t: monitoring.profile(v) for t, v in transition_scores(got).items()}}  # fmt: skip
        with open(base_path, "w") as fh:
            json.dump(baseline, fh, indent=1)
        print("baseline saved from", b_ym)
    baseline = json.load(open(base_path))
    m0 = rb.latest_month(spark)
    economy = str(nat_m["month"].max())[:7].replace("-", "")
    _, got = scores(m0, economy if economy > m0 else None, paths=8)
    psis = {
        t: monitoring.psi(baseline["profiles"][t], v)
        for t, v in transition_scores(got).items()
        if t in baseline["profiles"]
    }
    record = {"date": date.today().isoformat(), "book_month": m0, "economy_month": economy, "models": versions,
              "baseline_ym": baseline["baseline_ym"], "psi": psis,
              "psi_status": {t: monitoring.psi_status(v) for t, v in psis.items()},
              "code_version": dbutils.widgets.get("code_version")}  # fmt: skip
    with open(f"{OUT}/weekly/{record['date']}.json", "w") as fh:
        json.dump(record, fh, indent=1)
    print(json.dumps(record, indent=1))
    dbutils.notebook.exit(json.dumps(record))

# COMMAND ----------

R = dbutils.widgets.get("reserve_ym").strip()
if not close.is_quarter_end(R):
    raise ValueError(f"quarterly monitoring needs reserve_ym = a quarter-end month, got {R!r}")
first_m, _ = close.quarter_months(R)
all_cells, labels, probs = [], {}, {}
for j in range(3):
    m0 = multistate.add_months(first_m, j - 1)  # starts: the month before each month of the quarter
    start, got = scores(m0)
    nxt = spark.sql(f"select loan_id, status, least(effective_months_late, 2) as late from {GOLD}.loan_panel "
                    f"where period_ym = '{multistate.add_months(m0, 1)}'").toPandas().set_index("loan_id")  # fmt: skip
    for s, (idx, P) in got.items():
        loans = start.iloc[idx]
        row = nxt.reindex(loans["loan_id"])
        keep = row["status"].notna().to_numpy() & (row["status"] != "censored").to_numpy()
        actual = np.where(row["status"] == "defaulted", fx.DEFAULTED, np.where(row["status"] == "prepaid", fx.PREPAID,
                          row["late"].fillna(0).to_numpy()))[keep].astype(int)  # fmt: skip
        Pk, loans = P[keep], loans[keep]
        groups = {"year": [multistate.add_months(m0, 1)[:4]] * len(loans), "vintage": loans["vintage_year"].astype(int).tolist(),
                  "start_state": [fx.STATES[s]] * len(loans)}  # fmt: skip
        for dest in (fx.DEFAULTED, fx.PREPAID):
            t = f"{fx.STATES[s]}->{fx.STATES[dest]}"
            labels.setdefault(t, []).append(actual == dest)
            probs.setdefault(t, []).append(Pk[:, dest])
            for by, g in groups.items():
                all_cells += monitoring.cells(t, by, g, actual == dest, Pk[:, dest])


def merge(cells):
    """The same cell from the three months, added up."""
    acc = {}
    for c in cells:
        key = (c.transition, c.by, c.key)
        a = acc.get(key)
        acc[key] = (
            c
            if a is None
            else monitoring.Cell(c.transition, c.by, c.key, a.actual + c.actual, a.predicted + c.predicted)
        )
    return list(acc.values())


cells = merge(all_cells)
aucs = {}
for t in labels:
    y, p = np.concatenate(labels[t]), np.concatenate(probs[t])
    aucs[t] = float(metrics.auc(y.astype(int), p)) if 0 < y.sum() < len(y) else float("nan")
weekly = sorted(os.listdir(f"{OUT}/weekly"))
latest_psi = json.load(open(f"{OUT}/weekly/{weekly[-1]}"))["psi"] if weekly else {}
summary = monitoring.quarterly_summary(R, cells, aucs, latest_psi)
summary["models"] = versions
trend_path = f"{OUT}/trend.json"
trend = json.load(open(trend_path)) if os.path.exists(trend_path) else []
trend = [t for t in trend if t["quarter"] != R] + [{k: summary[k] for k in ("quarter", "default_cells_failed", "auc", "psi")}
                                                   | {"failed_cells": len(summary["failed_cells"])}]  # fmt: skip
trained = datetime.strptime(dbutils.widgets.get("trained_on"), "%Y-%m-%d").date()
summary["retrain"] = monitoring.retrain_due(trained, date.today(), trend)
summary["alerts"] = monitoring.alerts(summary)
with open(f"{OUT}/quarterly/{R}.json", "w") as fh:
    json.dump(summary, fh, indent=1, default=float)
with open(trend_path, "w") as fh:
    json.dump(sorted(trend, key=lambda t: t["quarter"]), fh, indent=1, default=float)
out = {k: summary[k] for k in ("quarter", "default_cells_failed", "auc", "retrain", "alerts")}
print(json.dumps(out, indent=1, default=float))
dbutils.notebook.exit(json.dumps(out, default=float))
