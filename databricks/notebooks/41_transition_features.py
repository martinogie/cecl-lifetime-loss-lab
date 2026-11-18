# Databricks notebook source
# MAGIC %md
# MAGIC ### 41 transition features: the multi-state tables (ADR-011; Phase 2 plan, multi-state, M2-M3)
# MAGIC Thin wrapper around `ingestion.feature_build`, the same SQL the PC runs with DuckDB. Per vintage:
# MAGIC - the **transition table**: every live loan-month with its starting state and this month's state;
# MAGIC   current -> current months sampled 1 in 25 and weighted, every other move kept (test months to a
# MAGIC   separate holdout table, read only by the test command, ADR-009 E);
# MAGIC - the **projection tables** for the 2017-2019 check: every loan alive in January 2017 with its
# MAGIC   start-month inputs, and its actual outcome to December 2019.
# MAGIC Tie-outs per vintage (every move vs the panel, every month in one starting state, moves into
# MAGIC default and prepaid vs the panel) stop the job before anything reaches the real tables; then the
# MAGIC contract on the features and the new state columns. With `export_slice = true` the vintages' rows
# MAGIC are also written as Parquet to the volume for the row-by-row comparison with the PC.
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
TRANSITIONS = {False: f"{GOLD}.transitions", True: f"{GOLD}.transitions_holdout"}
PROJECTION = {"start": f"{GOLD}.projection_start_validate", "actuals": f"{GOLD}.projection_actuals_validate"}
STAGING = f"{GOLD}.transitions_staging"


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


def replace_vintage(df, table: str, vintage: int) -> int:
    writer = df.write.format("delta").partitionBy("vintage_year").option("mergeSchema", "true")
    if spark.catalog.tableExists(table):
        writer.mode("overwrite").option("replaceWhere", f"vintage_year = {vintage}").saveAsTable(table)
    else:
        writer.saveAsTable(table)
    return spark.table(table).where(F.col("vintage_year") == vintage).count()


checks, report = [], {}
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
for vintage in years:
    rel = fb.databricks_relations(environment.databricks_schema, vintage)
    # Computed once into a staging table (serverless can't cache), tied out there, then copied.
    spark.sql(fb.transitions_sql(vintage, rel, "spark")).write.mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(STAGING)
    panel = tuple(spark.sql(fb.panel_counts_sql(rel["panel"])).collect()[0])
    panel_moves = {
        (int(r[0]), int(r[1])): int(r[2]) for r in spark.sql(fb.panel_moves_sql(rel["panel"])).collect()
    }
    table_moves = {(int(r[0]), int(r[1])): float(r[3]) for r in spark.sql(fb.moves_sql(STAGING)).collect()}
    vintage_checks = fb.transition_tie_outs(vintage, panel, panel_moves, table_moves)
    checks += vintage_checks
    controls.enforce(vintage_checks)  # stop before anything reaches the real tables
    staged = spark.table(STAGING)
    holdout = F.col("split").isin(list(fx.HOLDOUT))
    written = {TRANSITIONS[False]: replace_vintage(staged.where(~holdout), TRANSITIONS[False], vintage),
               TRANSITIONS[True]: replace_vintage(staged.where(holdout), TRANSITIONS[True], vintage)}  # fmt: skip
    start = spark.sql(fb.projection_start_sql(vintage, rel, "spark"))
    actuals = spark.sql(fb.projection_actuals_sql(rel["panel"])).withColumn("vintage_year", F.lit(vintage))
    written[PROJECTION["start"]] = replace_vintage(start, PROJECTION["start"], vintage)
    written[PROJECTION["actuals"]] = replace_vintage(actuals, PROJECTION["actuals"], vintage)
    if export:
        base = f"{VOLUME}/results/transitions_slice/{stamp}"
        staged.write.mode("overwrite").parquet(f"{base}/transitions/vintage={vintage}")
        spark.table(PROJECTION["start"]).where(F.col("vintage_year") == vintage).write.mode(
            "overwrite"
        ).parquet(f"{base}/projection_start/vintage={vintage}")
    report[str(vintage)] = written
    print(vintage, written)
spark.sql(f"drop table if exists {STAGING}")

# Contract over everything written for these vintages: the features' ranges and missing shares, and
# the new state columns (starting state current/30/60, destination one of the five states).
in_list = ", ".join(str(y) for y in years)
relation = (f"(select * from {TRANSITIONS[False]} where vintage_year in ({in_list}) union all "
            f"select * from {TRANSITIONS[True]} where vintage_year in ({in_list}))")  # fmt: skip
contract = spark.sql(fb.contract_sql(relation, fx.TRANSITION_FEATURES)).collect()[0].asDict()
problems = fb.contract_problems(contract, fx.TRANSITION_FEATURES)
bad_start, bad_state = spark.sql(fb.state_check_sql(relation)).collect()[0]
if bad_start or bad_state:
    problems.append(f"state columns: {bad_start} rows with a starting state outside current/30/60, "
                    f"{bad_state} rows with an unknown destination")  # fmt: skip
moves = {f"{fx.STATES[int(r[0])]} -> {fx.STATES[int(r[1])]}": {"rows": int(r[2]), "weighted": round(float(r[3]))}
         for r in spark.sql(fb.moves_sql(relation)).collect()}  # fmt: skip
result = {
    "stamp": stamp,
    "vintages": years,
    "tables": report,
    "tie_outs": [t.as_dict() for t in checks],
    "moves": moves,
    "contract": {"problems": problems,
                 "missing": {n: contract[f"{n}__missing"] for n in fx.TRANSITION_FEATURES if contract[f"{n}__missing"]}},
    "export": f"{VOLUME}/results/transitions_slice/{stamp}" if export else None,
}  # fmt: skip
os.makedirs(f"{VOLUME}/results", exist_ok=True)
with open(f"{VOLUME}/results/transitions_{stamp}.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=1, default=str)
spark.createDataFrame(
    [(stamp, os.environ["CECL_ENV"], "databricks_transition_features", json.dumps(result, default=str))],
    "run_id string, env string, pipeline string, record string",
).write.mode("append").saveAsTable(f"{OPS}.run_log")
print(f"tie-outs: {len(checks)} of {len(checks)} agree; contract problems: {problems or 'none'}")
if problems:
    raise ValueError("transition contract failed: " + "; ".join(problems))
dbutils.notebook.exit(json.dumps({"stamp": stamp, "tie_outs": len(checks), "vintages": len(years)}))
