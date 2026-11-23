"""The book at a reserve month, on Databricks (Spark): every loan alive at the end of the month, with its start
row for the projection, its history and its actual state at the end of the month.

Used by the reserve projection (notebook 65), the quarterly close's monitoring (notebook 69) and anything
else that needs "the book as it stood at the end of month R". Moved here unchanged from notebook 65 so the
same definition serves all of them.
"""

from __future__ import annotations

from typing import Any

from cecl import environment
from ingestion import feature_build as fb


def reserve_book(spark: Any, R: str, log: Any = print) -> tuple[Any, Any]:
    """Loans alive at the end of R: their start row for R, history, and actual state at the end of R."""
    gold = environment.databricks_schema("gold")
    parts = [spark.sql(fb.v2_projection_starts_sql(v, fb.databricks_relations(environment.databricks_schema, v), "spark", starts=(R,), keep_censored=True))
             for v in range(1999, int(R[:4]) + 1)]  # fmt: skip
    df = parts[0]
    for p in parts[1:]:
        df = df.unionByName(p, allowMissingColumns=True)
    start = df.toPandas()
    end = spark.sql(f"select loan_id, status, least(effective_months_late, 2) as first_state, "
                    f"case when flagged_pause then 1 else 0 end as first_pause from {gold}.loan_panel "
                    f"where period_ym = '{R}'").toPandas()  # fmt: skip
    last_month = spark.sql(f"select max(period_ym) from {gold}.loan_panel").collect()[0][0]
    alive = end["status"].isin(["current", "delinquent"]) | (
        (end["status"] == "censored") & (R == last_month)
    )
    log(
        R,
        "start rows",
        len(start),
        "| panel statuses",
        end["status"].value_counts().to_dict(),
        "| alive",
        int(alive.sum()),
    )
    start = start.merge(end[alive][["loan_id", "first_state", "first_pause"]], on="loan_id", how="inner")
    if start.empty:
        raise ValueError(f"reserve book for {R} is empty: stopping")
    log(R, "book", len(start), "balance", float(start["upb_start"].sum()), "states", start["property_state"].nunique(),
        "upb_start nulls", int(start["upb_start"].isna().sum()))  # fmt: skip
    start = start.sort_values("loan_id").reset_index(drop=True)
    history = spark.sql(fb.v2_projection_history_sql(f"{gold}.loan_panel", starts=(R,))).toPandas()
    return start, history


def latest_month(spark: Any) -> str:
    """The newest month in the prod panel (the last month of Freddie's latest release)."""
    return str(
        spark.sql(f"select max(period_ym) from {environment.databricks_schema('gold')}.loan_panel").collect()[
            0
        ][0]
    )
