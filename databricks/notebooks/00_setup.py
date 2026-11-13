# Databricks notebook source
# MAGIC %md
# MAGIC ### 00 setup check (Phase 0 placeholder)
# MAGIC Thin wrapper: all logic is in the `cecl` package. Confirms which environment and which
# MAGIC catalog/schemas this run would use. Writes nothing.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import os

dbutils.widgets.text("env", "")
dbutils.widgets.text("layout", "catalogs")
# The job passes env and layout; cecl.environment refuses anything but dev or prod, so a run can't
# silently fall back to some default environment.
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

from cecl import environment

settings = environment.require_airflow_env()
print(environment.describe())
for layer in environment.LAYERS:
    print(f"{layer:7s} -> {environment.databricks_schema(layer)}")
print("model example ->", environment.registered_model("pd_hazard"))
