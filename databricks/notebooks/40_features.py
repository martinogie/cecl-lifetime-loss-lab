# Databricks notebook source
# MAGIC %md
# MAGIC ### 40 features: panel + silver + economic data -> the model's tables (Phase 2 step 2)
# MAGIC Thin wrapper around `ingestion.feature_build`, the same SQL the PC runs with DuckDB (only the
# MAGIC dialect differs). Per vintage: the sampled, weighted training table and the horizon table,
# MAGIC replaced by vintage (`replaceWhere`); the test months go to separate holdout tables, read only
# MAGIC by the test command (ADR-009 E). Tie-outs per vintage stop the job on any mismatch.
# MAGIC With `export_slice = true`, the vintages' rows are also written as Parquet to the volume, so the
# MAGIC PC can compare them row by row with its own build.
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import json
import os
from datetime import UTC, datetime

dbutils.widgets.text("env", "")
dbutils.widgets.text("layout", "catalogs")
dbutils.widgets.text("years", "")
dbutils.widgets.text("export_slice", "false")
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

from pyspark.sql import functions as F

from cecl import controls, environment
from cecl import features as fx
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, OPS = environment.databricks_schema("gold"), environment.databricks_schema("ops")
environment.assert_can_write(GOLD)
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
TABLES = {
    ("features", False): f"{GOLD}.features",
    ("features", True): f"{GOLD}.features_holdout",
    ("horizon", False): f"{GOLD}.features_horizon",
    ("horizon", True): f"{GOLD}.features_horizon_holdout",
}


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


years = parse_years(dbutils.widgets.get("years"))
export = dbutils.widgets.get("export_slice").lower() == "true"
print(environment.describe(), "| vintages:", years, "| export:", export)

# COMMAND ----------


def write(df, kind: str, vintage: int) -> dict[str, int]:
    """Write one vintage's rows, test months to the holdout table; returns rows per table."""
    counts = {}
    holdout = F.col("split").isin(list(fx.HOLDOUT))
    for is_holdout, part in ((False, df.where(~holdout)), (True, df.where(holdout))):
        table = TABLES[(kind, is_holdout)]
        # mergeSchema: a new feature column (e.g. relief_refinance, 17 Nov) is added to the table
        writer = part.write.format("delta").partitionBy("vintage_year").option("mergeSchema", "true")
        if spark.catalog.tableExists(table):
            writer.mode("overwrite").option("replaceWhere", f"vintage_year = {vintage}").saveAsTable(table)
        else:
            writer.saveAsTable(table)
        counts[table] = spark.table(table).where(F.col("vintage_year") == vintage).count()
    return counts


checks, report = [], {}
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
STAGING = {"features": f"{GOLD}.features_staging", "horizon": f"{GOLD}.features_horizon_staging"}
for vintage in years:
    rel = fb.databricks_relations(environment.databricks_schema, vintage)
    # Computed once into staging tables (serverless compute can't cache), tied out there with plain
    # SQL totals, and only then copied into the real tables.
    for kind, sql in (("features", fb.training_sql(vintage, rel, "spark")),
                      ("horizon", fb.horizon_sql(vintage, rel, "spark"))):  # fmt: skip
        spark.sql(sql).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(STAGING[kind])
    panel = spark.sql(fb.panel_counts_sql(rel["panel"])).collect()[0]
    got = spark.sql(fb.totals_sql(STAGING["features"])).collect()[0]
    vintage_checks = fb.tie_outs(vintage, tuple(panel), got[0], got[1], float(got[2]))
    checks += vintage_checks
    controls.enforce(vintage_checks)  # stop before anything reaches the real tables
    training, horizon = spark.table(STAGING["features"]), spark.table(STAGING["horizon"])
    written = write(training, "features", vintage) | write(horizon, "horizon", vintage)
    if export:
        for kind, df in (("features", training), ("horizon", horizon)):
            df.write.mode("overwrite").parquet(
                f"{VOLUME}/results/features_slice/{stamp}/{kind}/vintage={vintage}"
            )
    report[str(vintage)] = written
    print(vintage, written)
for table in STAGING.values():
    spark.sql(f"drop table if exists {table}")

# Contract over everything written for these vintages (training and holdout tables).
in_list = ", ".join(str(y) for y in years)
relation = (f"(select * from {TABLES[('features', False)]} where vintage_year in ({in_list}) union all "
            f"select * from {TABLES[('features', True)]} where vintage_year in ({in_list}))")  # fmt: skip
contract = spark.sql(fb.contract_sql(relation)).collect()[0].asDict()
problems = fb.contract_problems(contract)
eltv = spark.sql(fb.eltv_check_sql(relation)).collect()[0]
result = {
    "stamp": stamp,
    "vintages": years,
    "tables": report,
    "tie_outs": [t.as_dict() for t in checks],
    "contract": {"problems": problems,
                 "missing": {n: contract[f"{n}__missing"] for n in fx.FEATURE_NAMES if contract[f"{n}__missing"]}},
    "eltv_check": [eltv[0], eltv[1], eltv[2], eltv[3]],
    "export": f"{VOLUME}/results/features_slice/{stamp}" if export else None,
}  # fmt: skip
os.makedirs(f"{VOLUME}/results", exist_ok=True)
with open(f"{VOLUME}/results/features_{stamp}.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=1, default=str)
spark.createDataFrame(
    [(stamp, os.environ["CECL_ENV"], "databricks_features", json.dumps(result, default=str))],
    "run_id string, env string, pipeline string, record string",
).write.mode("append").saveAsTable(f"{OPS}.run_log")
print(f"tie-outs: {len(checks)} of {len(checks)} agree; contract problems: {problems or 'none'}")
if problems:
    raise ValueError("feature contract failed: " + "; ".join(problems))
dbutils.notebook.exit(json.dumps({"stamp": stamp, "tie_outs": len(checks)}))
