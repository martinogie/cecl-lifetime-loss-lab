# Databricks notebook source
# MAGIC %md
# MAGIC ### 35 economic tables -> silver (Phase 2 step 2a)
# MAGIC The PC's silver economic tables (built by Airflow from FRED, FHFA and the Fed, Phase 1 step 7)
# MAGIC are uploaded as Parquet to the bronze volume (`raw/macro_silver/`) and loaded here into
# MAGIC `silver.macro_<table>`, replaced whole. Tie-out: rows in the table = rows in the file. The
# MAGIC row counts and value sums per variable are returned, so the PC can check them against its own.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os

dbutils.widgets.text("env", "")
dbutils.widgets.text("layout", "catalogs")
dbutils.widgets.text("tables", "")  # empty = the four v1 tables; e.g. "household_monthly" (model v2)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

from pyspark.sql import functions as F

from cecl import environment

environment.require_airflow_env()
SILVER = environment.databricks_schema("silver")
environment.assert_can_write(SILVER)
FILES = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw/macro_silver"
DEFAULT_TABLES = ("state_monthly", "national_monthly", "long_run_averages", "fed_paths")
KNOWN = DEFAULT_TABLES + ("household_monthly",)  # household_monthly: model v2's stress inputs
chosen = [t.strip() for t in dbutils.widgets.get("tables").split(",") if t.strip()]
TABLES = tuple(chosen) or DEFAULT_TABLES
if set(TABLES) - set(KNOWN):
    raise ValueError(f"unknown macro table(s): {sorted(set(TABLES) - set(KNOWN))}")

# COMMAND ----------

summary = {}
for name in TABLES:
    source = spark.read.parquet(f"{FILES}/{name}.parquet")
    target = f"{SILVER}.macro_{name}"
    source.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
    in_file, in_table = source.count(), spark.table(target).count()
    if in_file != in_table:  # control C02
        raise RuntimeError(f"tie-out failed: {target} has {in_table:,} rows, the file {in_file:,}")
    value = "path_value" if name == "fed_paths" else "value"
    keys = [c for c in ("scenario", "state", "geo") if c in source.columns][:1] + ["variable"]
    totals = spark.table(target).groupBy(*keys).agg(F.sum(value).alias("s")).collect()
    sums = {"|".join(str(r[k]) for k in keys): float(r["s"]) for r in totals}
    summary[name] = {"rows": in_table, "value_sums": sums}
    print(f"{target}: {in_table:,} rows (tie-out ok)")

dbutils.notebook.exit(json.dumps({"macro": summary}, default=str))
