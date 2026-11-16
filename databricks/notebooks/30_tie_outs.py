# Databricks notebook source
# MAGIC %md
# MAGIC ### 30 tie-outs: bronze vs silver vs panel on Databricks (control C02, Phase 1 step 9)
# MAGIC Thin wrapper around `ingestion.tie_outs.layer_tie_outs`: the same SQL as the PC, run with
# MAGIC Spark on the Delta tables. Writes every number (tie-outs, watch counts, how loans ended) to
# MAGIC the run log table and to a JSON file in the volume, so the PC can compare it with its own
# MAGIC run number for number. Any tie-out mismatch fails the task (after the results are saved).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
from datetime import UTC, datetime

dbutils.widgets.text("env", "")
dbutils.widgets.text("layout", "catalogs")
dbutils.widgets.text("years", "")
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

from cecl import controls, environment
from ingestion import tie_outs

environment.require_airflow_env()
OPS = environment.databricks_schema("ops")
environment.assert_can_write(OPS)
RESULTS = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw/results"


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


years = parse_years(dbutils.widgets.get("years"))
print(environment.describe(), "| vintages:", years)

# COMMAND ----------


def query(sql: str) -> list[tuple]:
    return [tuple(r) for r in spark.sql(sql).collect()]


relations = tie_outs.databricks_relations(environment.databricks_schema)
checks, watch, rows = tie_outs.layer_tie_outs(query, relations, years)
outcomes = tie_outs.panel_outcomes(query, relations["panel"], years)
result = tie_outs.comparable(checks, watch, outcomes) | {"rows": rows, "vintages": years}

# Save before enforcing, so a failed run still leaves its evidence.
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
os.makedirs(RESULTS, exist_ok=True)
path = f"{RESULTS}/tie_outs_{stamp}.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=1)
spark.createDataFrame(
    [(stamp, os.environ["CECL_ENV"], "databricks_tie_outs", json.dumps(result))],
    "run_id string, env string, pipeline string, record string",
).write.mode("append").saveAsTable(f"{OPS}.run_log")

failed = [t for t in checks if not t.ok]
print(f"tie-outs: {len(checks) - len(failed)} of {len(checks)} agree; results: {path}")
controls.enforce(checks)
dbutils.notebook.exit(json.dumps({"tie_outs": len(checks), "results": path}))
