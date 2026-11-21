# Databricks notebook source
# MAGIC %md
# MAGIC ### 67 Prod starting point: clone the checked dev data and models into the prod catalog (cutover, once)
# MAGIC Martins's decision (21 November 2026, option A): prod starts from the same, already checked data as dev, so
# MAGIC the dry-run close reproduces the 30 June 2026 reserve to the cent. Every later quarter is built by the prod
# MAGIC pipeline itself.
# MAGIC - every bronze, silver and gold table: `DEEP CLONE` from `cecl_lab_dev`, then a row-count tie-out per table
# MAGIC   (a mismatch stops the job);
# MAGIC - the volume's **inputs** only (Freddie zips, the economic tables); dev job outputs are not copied;
# MAGIC - the v2 transition models: their registered versions copied into the prod registry with their tags, still
# MAGIC   `@challenger` (v2 failed its holdout and is not champion);
# MAGIC - the prod run log starts empty (not cloned).
# MAGIC Bank Health's catalog is never read or written. Refuses to run unless CECL_ENV=prod.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os

for name, default in (
    ("env", ""),
    ("layout", "catalogs"),
    ("source_catalog", "cecl_lab_dev"),
    ("code_version", "unknown"),
):
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import mlflow
from mlflow import MlflowClient

from cecl import environment

if environment.current_env() != "prod":
    raise ValueError("67_clone_to_prod runs only with CECL_ENV=prod")
SRC = dbutils.widgets.get("source_catalog").strip()
DST = environment.databricks_schema("bronze").split(".")[0]
if SRC == DST or "bank_health" in (SRC + DST):
    raise ValueError(f"refusing to clone {SRC} -> {DST}")
SCHEMAS = ("bronze", "silver", "gold")
INPUTS = ("freddie", "macro_silver")  # volume folders that are pipeline inputs; results/ etc. are dev outputs
MODELS = ("pd_v2_transition_current", "pd_v2_transition_30", "pd_v2_transition_60")
summary = {"source": SRC, "target": DST, "tables": {}, "volume": {}, "models": {}}

# COMMAND ----------

for s in SCHEMAS:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {DST}.{s}")
    for row in spark.sql(f"SHOW TABLES IN {SRC}.{s}").collect():
        t = row["tableName"]
        spark.sql(f"CREATE OR REPLACE TABLE {DST}.{s}.{t} DEEP CLONE {SRC}.{s}.{t}")
        n_src = spark.table(f"{SRC}.{s}.{t}").count()
        n_dst = spark.table(f"{DST}.{s}.{t}").count()
        if n_src != n_dst:  # control C02: tie-out
            raise RuntimeError(f"tie-out failed: {DST}.{s}.{t} has {n_dst:,} rows, {SRC} {n_src:,}")
        summary["tables"][f"{s}.{t}"] = n_dst
        print(f"{s}.{t}: {n_dst:,} rows (tie-out ok)")

# COMMAND ----------

spark.sql(f"CREATE VOLUME IF NOT EXISTS {DST}.bronze.raw")
src_vol, dst_vol = f"/Volumes/{SRC}/bronze/raw", f"/Volumes/{DST}/bronze/raw"
for folder in INPUTS:
    try:
        files = dbutils.fs.ls(f"{src_vol}/{folder}")
    except Exception:  # noqa: BLE001 (a folder that does not exist in dev is simply skipped)
        continue
    dbutils.fs.cp(f"{src_vol}/{folder}", f"{dst_vol}/{folder}", recurse=True)

    def count(path):
        return sum(count(f.path) if f.isDir() else 1 for f in dbutils.fs.ls(path))

    n_src, n_dst = count(f"{src_vol}/{folder}"), count(f"{dst_vol}/{folder}")
    if n_src != n_dst:
        raise RuntimeError(f"volume copy incomplete: {folder} {n_dst} of {n_src} files")
    summary["volume"][folder] = n_dst
    print(f"volume {folder}: {n_dst} files copied")

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()
for m in MODELS:
    src_name, dst_name = f"{SRC}.ml.{m}", f"{DST}.ml.{m}"
    src = client.get_model_version_by_alias(src_name, "challenger")
    new = client.copy_model_version(src_model_uri=f"models:/{src_name}/{src.version}", dst_name=dst_name)
    for k, v in (src.tags or {}).items():
        client.set_model_version_tag(dst_name, new.version, k, v)
    client.set_model_version_tag(dst_name, new.version, "copied_from", f"{src_name} v{src.version}")
    client.set_registered_model_alias(dst_name, "challenger", new.version)
    summary["models"][m] = {
        "version": new.version,
        "from": f"{src_name} v{src.version}",
        "run_id": src.run_id,
    }
    print(f"model {dst_name} v{new.version} @challenger (from {src_name} v{src.version})")

summary["code_version"] = dbutils.widgets.get("code_version")
dbutils.notebook.exit(json.dumps(summary, default=str))
