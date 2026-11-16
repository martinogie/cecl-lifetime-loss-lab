"""Pipeline tie-outs for Freddie's data (control C02), run on the local DuckDB database.

For each vintage, the same totals are counted in each layer and must agree exactly:

| Tie-out | Expected (earlier layer) | Actual (later layer) |
|---|---|---|
| loans | bronze origination rows | silver loans (`stg_freddie__origination`) |
| original balance | bronze sum of original UPB | silver sum of original UPB, to the cent |
| loan-months | bronze monthly rows | silver loan-months (`int_freddie__loan_months`) |
| monthly balance | bronze sum of current UPB | silver sum of current UPB, to the cent |
| loans in the panel | silver loans with monthly rows | distinct loans in the panel |
| loans accounted for | silver loans | panel loans + loans with no monthly rows yet (L-015) |
| one ending per loan | panel loans | panel rows marked defaulted, prepaid or censored |

The bronze loader already ties each file to its contract check's row count (`ingestion/freddie.py`),
and the panel builder ties each vintage before keeping it (`ingestion/panel_build.py`); these
checks tie the layers to each other after dbt, so a filter or join that silently drops or doubles
rows is caught.

Also counted for the run log (the limitations log asks to watch them; not tie-outs):
L-013 rows whose remaining months were derived, L-014 loans with terms over 360 months, L-015 loans
with no monthly rows yet, L-018 loans that came in another year's sample file, and loans whose first
month was XX at loan age 0 ("not yet due").

Vintage, on every side, is the year in the loan ID (L-018), as in the dbt staging models.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from cecl.controls import TieOut

FINAL = ("defaulted", "prepaid", "censored")
# A loan's vintage is the year in its loan ID (PYYQn..., L-018), the same rule as the dbt staging
# models; bronze is grouped the same way, not by the sample file's year (one 2008-file loan has a
# 2009 ID). Loans whose file year differs are counted for the run log, not hidden.
ID_VINTAGE = (
    "case when substr(loan_id, 2, 2) = '99' then 1999 else 2000 + cast(substr(loan_id, 2, 2) as integer) end"
)


def _years(vintages: Sequence[int]) -> str:
    if not vintages:
        raise ValueError("no vintages to tie out")
    return ", ".join(str(int(v)) for v in vintages)  # ints only: safe to place in SQL


def _dec(value: Any) -> Decimal:
    return Decimal(0) if value is None else Decimal(value)


Query = Callable[[str], list[tuple[Any, ...]]]  # SQL text -> result rows (DuckDB or Spark)


def databricks_relations(schema: Callable[[str], str]) -> dict[str, str]:
    """The same layers on Databricks. `schema` is cecl.environment.databricks_schema, so both
    catalog layouts work (cecl_lab_dev.bronze or workspace.cecl_lab_dev_bronze). The same SQL runs
    there and on the PC: only these names differ."""
    return {
        "silver": schema("silver"),
        "bronze_orig": f"{schema('bronze')}.freddie_origination",
        "bronze_perf": f"{schema('bronze')}.freddie_performance",
        "panel": f"{schema('gold')}.loan_panel",
    }


def local_relations(bronze: Path, panel: Path) -> dict[str, str]:
    """The same layers on the PC: bronze and panel Parquet read in place by DuckDB."""
    b, p = Path(bronze).as_posix(), Path(panel).as_posix()
    return {
        "silver": "silver",
        "bronze_orig": f"read_parquet('{b}/freddie_orig/*/*.parquet', hive_partitioning = true)",
        "bronze_perf": f"read_parquet('{b}/freddie_perf/*/*.parquet', hive_partitioning = true)",
        "panel": f"read_parquet('{p}/vintage=*/panel.parquet')",
    }


def freddie_tie_outs(
    con: Any, bronze: Path, panel: Path, vintages: Sequence[int]
) -> tuple[list[TieOut], dict[str, Any], dict[str, int]]:
    """Local version: `con` is an open DuckDB connection to the database dbt built."""
    return layer_tie_outs(lambda sql: con.execute(sql).fetchall(), local_relations(bronze, panel), vintages)


def layer_tie_outs(
    query: Query, relations: dict[str, str], vintages: Sequence[int]
) -> tuple[list[TieOut], dict[str, Any], dict[str, int]]:
    """(tie-outs, watch counts by vintage, rows per layer) for the chosen vintages.

    `query` runs SQL; `relations` names the silver schema and the bronze and panel tables
    (local_relations or databricks_relations).
    """
    years = _years(vintages)
    silver = relations["silver"]
    bronze_orig, bronze_perf, panel_rows = (
        relations["bronze_orig"],
        relations["bronze_perf"],
        relations["panel"],
    )

    def by_vintage(sql: str) -> dict[int, tuple[Any, ...]]:
        return {int(r[0]): tuple(r[1:]) for r in query(sql)}

    b_orig = by_vintage(
        f"select {ID_VINTAGE} as v, count(*), sum(orig_upb), count(*) filter (where vintage <> {ID_VINTAGE}) "
        f"from {bronze_orig} where {ID_VINTAGE} in ({years}) group by 1"
    )
    s_orig = by_vintage(
        "select vintage_year, count(*), sum(orig_upb), "
        f"count(*) filter (where term_over_360) from {silver}.stg_freddie__origination "
        f"where vintage_year in ({years}) group by 1"
    )
    b_perf = by_vintage(
        f"select {ID_VINTAGE} as v, count(*), sum(current_upb) from {bronze_perf} "
        f"where {ID_VINTAGE} in ({years}) group by 1"
    )
    s_perf = by_vintage(
        "select vintage_year, count(*), sum(current_upb), count(distinct loan_id), "
        f"count(*) filter (where remaining_months_derived) from {silver}.int_freddie__loan_months "
        f"where vintage_year in ({years}) group by 1"
    )
    no_months = by_vintage(
        f"select o.vintage_year, count(*) from {silver}.stg_freddie__origination o "
        f"where o.vintage_year in ({years}) and not exists "
        f"(select 1 from {silver}.stg_freddie__performance m where m.loan_id = o.loan_id) group by 1"
    )
    pan = by_vintage(
        f"select vintage_year, count(*), count(distinct loan_id), "
        f"count(*) filter (where status in {FINAL}), "
        f"count(distinct loan_id) filter (where status_not_yet_due) from {panel_rows} "
        f"where vintage_year in ({years}) group by 1"
    )

    tie_outs: list[TieOut] = []
    watch: dict[str, Any] = {}
    rows = {"bronze_orig": 0, "bronze_perf": 0, "silver_loans": 0, "silver_loan_months": 0, "panel_rows": 0}
    for v in sorted(int(x) for x in vintages):
        bo, so = b_orig.get(v, (0, None, 0)), s_orig.get(v, (0, None, 0))
        bp, sp = b_perf.get(v, (0, None)), s_perf.get(v, (0, None, 0, 0))
        pn, nm = pan.get(v, (0, 0, 0, 0)), no_months.get(v, (0,))[0]
        tie_outs += [
            TieOut(f"vintage {v}: loans, bronze vs silver", int(bo[0]), int(so[0])),
            TieOut(f"vintage {v}: original balance, bronze vs silver", _dec(bo[1]), _dec(so[1])),
            TieOut(f"vintage {v}: loan-months, bronze vs silver", int(bp[0]), int(sp[0])),
            TieOut(f"vintage {v}: monthly balance, bronze vs silver", _dec(bp[1]), _dec(sp[1])),
            TieOut(f"vintage {v}: loans with monthly rows, silver vs panel", int(sp[2]), int(pn[1])),
            TieOut(f"vintage {v}: loans accounted for (panel + no monthly rows)", int(so[0]), int(pn[1]) + nm),
            TieOut(f"vintage {v}: one ending per panel loan", int(pn[1]), int(pn[2])),
        ]  # fmt: skip
        watch[str(v)] = {
            "L-013_remaining_months_derived_rows": int(sp[3]),
            "L-014_loans_term_over_360": int(so[2]),
            "L-015_loans_without_monthly_rows": int(nm),
            "L-018_loans_from_another_years_file": int(bo[2]),
            "xx_at_age_0_loans": int(pn[3]),
        }
        rows["bronze_orig"] += int(bo[0])
        rows["bronze_perf"] += int(bp[0])
        rows["silver_loans"] += int(so[0])
        rows["silver_loan_months"] += int(sp[0])
        rows["panel_rows"] += int(pn[0])
    return tie_outs, watch, rows


def panel_outcomes(query: Query, panel: str, vintages: Sequence[int]) -> dict[str, dict[str, int]]:
    """How each vintage's loans ended (defaulted / prepaid / censored), from the panel's final rows:
    the basis of default rates by vintage, compared between the PC and Databricks."""
    rows = query(
        f"select vintage_year, status, count(*) from {panel} "
        f"where vintage_year in ({_years(vintages)}) and status in {FINAL} group by 1, 2"
    )
    out: dict[str, dict[str, int]] = {str(int(v)): dict.fromkeys(FINAL, 0) for v in vintages}
    for v, status, n in rows:
        out[str(int(v))][str(status)] = int(n)
    return out


def comparable(tie_outs: Sequence[TieOut], watch: dict[str, Any], outcomes: dict[str, Any]) -> dict[str, Any]:
    """Every number behind a run's tie-outs, as plain JSON: two runs (PC and Databricks) on the same
    data must produce identical dictionaries."""
    return {
        "tie_outs": {t.name: [str(t.expected), str(t.actual)] for t in tie_outs},
        "watch": watch,
        "outcomes": outcomes,
    }


def differences(a: dict[str, Any], b: dict[str, Any], path: str = "") -> list[str]:
    """Where two `comparable` results differ (empty list: identical). Numbers are compared as
    exact decimals, so 100.10 and 100.1000 are equal."""
    found: list[str] = []
    for key in sorted(set(a) | set(b)):
        where = f"{path}/{key}"
        if key not in a or key not in b:
            found.append(f"{where}: only in {'second' if key not in a else 'first'}")
        elif isinstance(a[key], dict) and isinstance(b[key], dict):
            found += differences(a[key], b[key], where)
        elif _norm(a[key]) != _norm(b[key]):
            found.append(f"{where}: {a[key]} vs {b[key]}")
    return found


def _norm(value: Any) -> Any:
    if isinstance(value, list):
        return [_norm(v) for v in value]
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return value


def totals(watch: dict[str, Any]) -> dict[str, int]:
    """Watch counts summed over vintages, for the run summary."""
    out: dict[str, int] = {}
    for counts in watch.values():
        for k, n in counts.items():
            out[k] = out.get(k, 0) + n
    return out
