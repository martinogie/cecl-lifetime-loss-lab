# Databricks notebook source
# MAGIC %md
# MAGIC ### 42 transition features, model v2 (ADR-009 Amendment 3; docs/phase2/model_v2_plan.md)
# MAGIC Thin wrapper around `ingestion.feature_build` with `v2=True`: the same SQL as v1 plus the longer-memory
# MAGIC inputs (worst state in 6 months, months since the first missed payment, a payment pause in the last
# MAGIC 12 months) and the household-stress candidates, labelled with the v2 split. v1's tables are not touched.
# MAGIC Per vintage:
# MAGIC - **transitions_v2** (training months: 1999-2019 and July 2022 - June 2024; the COVID months are kept
# MAGIC   for reference, never fitted) and **transitions_v2_holdout** (July 2024 - June 2026, read only by the
# MAGIC   holdout command);
# MAGIC - the **walk-forward start tables**: every loan alive in January 2008, 2011, 2014, 2017 and July 2022,
# MAGIC   its outcome in each window, and its 12 months of history (states and pauses) for the path simulation.
# MAGIC Tie-outs per vintage (as v1) stop the job before anything reaches the real tables; then the contract.
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

from pyspark.sql import functions as F

from cecl import controls, environment
from cecl import features as fx
from ingestion import feature_build as fb

environment.require_airflow_env()
GOLD, OPS = environment.databricks_schema("gold"), environment.databricks_schema("ops")
environment.assert_can_write(GOLD)
VOLUME = "/Volumes/" + environment.databricks_schema("bronze").replace(".", "/") + "/raw"
TRANSITIONS = {False: f"{GOLD}.transitions_v2", True: f"{GOLD}.transitions_v2_holdout"}
STARTS = {"start": f"{GOLD}.projection_v2_start", "actuals": f"{GOLD}.projection_v2_actuals",
          "history": f"{GOLD}.projection_v2_history"}  # fmt: skip
STAGING = f"{GOLD}.transitions_v2_staging"
CONTRACT_FEATURES = fx.V2_STORED + tuple(f.name for f in fx.V2_STRESS)


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        years.extend(range(int(a), int(b or a) + 1))
    return years


years = parse_years(dbutils.widgets.get("years"))
print(environment.describe(), "| vintages:", years)

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
    spark.sql(fb.transitions_sql(vintage, rel, "spark", v2=True)).write.mode("overwrite").option(
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
    holdout = F.col("split").isin(list(fx.V2_HOLDOUT_SPLITS))
    written = {TRANSITIONS[False]: replace_vintage(staged.where(~holdout), TRANSITIONS[False], vintage),
               TRANSITIONS[True]: replace_vintage(staged.where(holdout), TRANSITIONS[True], vintage)}  # fmt: skip
    start = spark.sql(fb.v2_projection_starts_sql(vintage, rel, "spark"))
    actuals = spark.sql(fb.v2_projection_actuals_sql(rel["panel"])).withColumn("vintage_year", F.lit(vintage))
    history = spark.sql(fb.v2_projection_history_sql(rel["panel"])).withColumn("vintage_year", F.lit(vintage))
    written[STARTS["start"]] = replace_vintage(start, STARTS["start"], vintage)
    written[STARTS["actuals"]] = replace_vintage(actuals, STARTS["actuals"], vintage)
    written[STARTS["history"]] = replace_vintage(history, STARTS["history"], vintage)
    report[str(vintage)] = written
    print(vintage, written)
spark.sql(f"drop table if exists {STAGING}")

in_list = ", ".join(str(y) for y in years)
relation = (f"(select * from {TRANSITIONS[False]} where vintage_year in ({in_list}) union all "
            f"select * from {TRANSITIONS[True]} where vintage_year in ({in_list}))")  # fmt: skip
contract = spark.sql(fb.contract_sql(relation, CONTRACT_FEATURES)).collect()[0].asDict()
problems = fb.contract_problems(contract, CONTRACT_FEATURES)
bad_start, bad_state = spark.sql(fb.state_check_sql(relation)).collect()[0]
if bad_start or bad_state:
    problems.append(f"state columns: {bad_start} rows with a starting state outside current/30/60, "
                    f"{bad_state} rows with an unknown destination")  # fmt: skip
splits = {
    r[0]: int(r[1]) for r in spark.sql(f"select split, count(*) from {relation} group by split").collect()
}
starts = {r[0]: int(r[1]) for r in spark.sql(
    f"select start_ym, count(*) from {STARTS['start']} where vintage_year in ({in_list}) group by start_ym").collect()}  # fmt: skip
result = {
    "stamp": stamp,
    "model_version": "v2",
    "vintages": years,
    "tables": report,
    "rows_by_split": splits,
    "start_loans": starts,
    "tie_outs": [t.as_dict() for t in checks],
    "contract": {"problems": problems,
                 "missing": {n: contract[f"{n}__missing"] for n in CONTRACT_FEATURES if contract[f"{n}__missing"]}},
}  # fmt: skip
os.makedirs(f"{VOLUME}/results", exist_ok=True)
with open(f"{VOLUME}/results/transitions_v2_{stamp}.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=1, default=str)
spark.createDataFrame(
    [(stamp, os.environ["CECL_ENV"], "databricks_transition_features_v2", json.dumps(result, default=str))],
    "run_id string, env string, pipeline string, record string",
).write.mode("append").saveAsTable(f"{OPS}.run_log")
print(f"tie-outs: {len(checks)} of {len(checks)} agree; contract problems: {problems or 'none'}")
print(json.dumps({"rows_by_split": splits, "start_loans": starts}, indent=1))
if problems:
    raise ValueError("transition v2 contract failed: " + "; ".join(problems))
dbutils.notebook.exit(json.dumps({"stamp": stamp, "tie_outs": len(checks), "vintages": len(years)}))
