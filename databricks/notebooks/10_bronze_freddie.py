# Databricks notebook source
# MAGIC %md
# MAGIC ### 10 bronze: Freddie zips -> Delta (Phase 1 step 9)
# MAGIC Thin wrapper around `ingestion.freddie.load_zip`, the same code as the PC: data contract per
# MAGIC file first (a failure stops before anything is written), then the file streamed from the zip
# MAGIC (never unzipped) into Parquet, then a row tie-out against the contract's count. Here the
# MAGIC Parquet is then loaded into the bronze Delta tables, one vintage at a time (`replaceWhere`,
# MAGIC so a re-run replaces that vintage only), and tied out again: Delta rows = file rows.
# MAGIC
# MAGIC Input: the sample zips Martins uploaded to the bronze volume (`raw/freddie/sample/`).
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
import shutil
import tempfile

dbutils.widgets.text("env", "")
dbutils.widgets.text("layout", "catalogs")
dbutils.widgets.text("years", "")
dbutils.widgets.text("release", "48")
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

from pyspark.sql import functions as F

from cecl import environment
from ingestion import freddie

environment.require_airflow_env()  # dev or prod only, never a guessed environment
BRONZE = environment.databricks_schema("bronze")
environment.assert_can_write(BRONZE)  # dev can never write prod
VOLUME = "/Volumes/" + BRONZE.replace(".", "/") + "/raw"
ZIPS, STAGING = f"{VOLUME}/freddie/sample", f"{VOLUME}/staging"
TABLES = {"orig": f"{BRONZE}.freddie_origination", "perf": f"{BRONZE}.freddie_performance"}


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


years = parse_years(dbutils.widgets.get("years"))
release = int(dbutils.widgets.get("release"))
print(environment.describe(), "| vintages:", years)

# COMMAND ----------

results = []
os.makedirs(STAGING, exist_ok=True)
for year in years:
    local = tempfile.mkdtemp(prefix=f"cecl_bronze_{year}_")
    try:
        # Contracts, Parquet and the row tie-out: exactly the PC's code path (raises on any failure).
        for f in freddie.load_zip(f"{ZIPS}/sample_{year}.zip", release, local):
            staged = f"{STAGING}/{f.path.name}"
            shutil.copyfile(f.path, staged)  # Spark reads from the volume, not the driver's disk
            df = spark.read.parquet(staged).withColumn("vintage", F.lit(year).cast("int"))
            table = TABLES[f.kind]
            writer = df.write.format("delta").partitionBy("vintage")
            if spark.catalog.tableExists(table):
                writer.mode("overwrite").option("replaceWhere", f"vintage = {year}").saveAsTable(table)
            else:
                writer.saveAsTable(table)
            in_delta = spark.table(table).where(F.col("vintage") == year).count()
            if in_delta != f.rows:  # control C02 on Databricks
                raise RuntimeError(
                    f"tie-out failed: {table} vintage {year}: {in_delta:,} rows, file {f.rows:,}"
                )
            os.remove(staged)
            results.append({"vintage": year, "table": table, "rows": f.rows})
            print(f"{year} {f.kind}: {f.rows:,} rows (contract, file and Delta tie-outs ok)")
    finally:
        shutil.rmtree(local, ignore_errors=True)

dbutils.notebook.exit(json.dumps({"bronze": results}))
