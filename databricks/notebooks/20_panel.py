# Databricks notebook source
# MAGIC %md
# MAGIC ### 20 panel: silver loan-months -> gold.loan_panel (Phase 1 step 9)
# MAGIC Thin wrapper: the ADR-002 rules are `cecl.panel`, applied one loan at a time by
# MAGIC `ingestion.panel_build.loan_panel_rows`, the same code the PC uses. Spark only splits the work
# MAGIC by loan (`applyInPandas`). Written per vintage with `replaceWhere`, so a re-run replaces only
# MAGIC the vintages it was given. The tie-outs run in the next task.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os

dbutils.widgets.text("env", "")
dbutils.widgets.text("layout", "catalogs")
dbutils.widgets.text("years", "")
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import pandas as pd
import pyarrow as pa
from pyspark.sql import functions as F

from cecl import environment
from ingestion import panel_build

environment.require_airflow_env()
SILVER, GOLD = environment.databricks_schema("silver"), environment.databricks_schema("gold")
environment.assert_can_write(GOLD)
TABLE = f"{GOLD}.loan_panel"


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


years = parse_years(dbutils.widgets.get("years"))
print(environment.describe(), "| vintages:", years)

# The output schema is the local panel's (ingestion/panel_build.PANEL_SCHEMA), as Spark DDL.
SPARK_TYPE = {
    pa.string(): "string",
    pa.int32(): "int",
    pa.bool_(): "boolean",
    pa.decimal128(18, 2): "decimal(18,2)",
}
DDL = ", ".join(f"{f.name} {SPARK_TYPE[f.type]}" for f in panel_build.PANEL_SCHEMA)
COLUMNS = panel_build.PANEL_SCHEMA.names

# COMMAND ----------


def one_loan(rows: pd.DataFrame) -> pd.DataFrame:
    """All of one loan's silver rows -> its panel rows (cecl.panel rules)."""
    return pd.DataFrame(panel_build.loan_panel_rows(rows.to_dict("records")), columns=COLUMNS)


source = (
    spark.table(f"{SILVER}.int_freddie__loan_months")
    .where(F.col("vintage_year").isin(years))
    .select(*panel_build.SOURCE_COLUMNS)
)
panel = source.groupBy("loan_id").applyInPandas(one_loan, schema=DDL)
writer = panel.write.format("delta").partitionBy("vintage_year")
if spark.catalog.tableExists(TABLE):
    in_list = ", ".join(str(y) for y in years)
    writer.mode("overwrite").option("replaceWhere", f"vintage_year in ({in_list})").saveAsTable(TABLE)
else:
    writer.saveAsTable(TABLE)

counts = {
    str(r["vintage_year"]): {"rows": r["rows"], "loans": r["loans"]}
    for r in spark.table(TABLE)
    .where(F.col("vintage_year").isin(years))
    .groupBy("vintage_year")
    .agg(F.count("*").alias("rows"), F.countDistinct("loan_id").alias("loans"))
    .collect()
}
print(counts)
dbutils.notebook.exit(json.dumps({"panel": counts}))
