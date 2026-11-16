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

from collections.abc import Sequence
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


def freddie_tie_outs(
    con: Any, bronze: Path, panel: Path, vintages: Sequence[int]
) -> tuple[list[TieOut], dict[str, Any], dict[str, int]]:
    """(tie-outs, watch counts by vintage, rows per layer) for the chosen vintages.

    `con` is an open DuckDB connection to the database dbt built (schema `silver`).
    """
    years = _years(vintages)
    b = Path(bronze).as_posix()
    p = Path(panel).as_posix()
    bronze_orig = f"read_parquet('{b}/freddie_orig/*/*.parquet', hive_partitioning = true)"
    bronze_perf = f"read_parquet('{b}/freddie_perf/*/*.parquet', hive_partitioning = true)"
    panel_rows = f"read_parquet('{p}/vintage=*/panel.parquet')"

    def by_vintage(sql: str) -> dict[int, tuple[Any, ...]]:
        return {int(r[0]): tuple(r[1:]) for r in con.execute(sql).fetchall()}

    b_orig = by_vintage(
        f"select {ID_VINTAGE} as v, count(*), sum(orig_upb), count(*) filter (where vintage <> {ID_VINTAGE}) "
        f"from {bronze_orig} where {ID_VINTAGE} in ({years}) group by 1"
    )
    s_orig = by_vintage(
        "select vintage_year, count(*), sum(orig_upb), "
        "count(*) filter (where term_over_360) from silver.stg_freddie__origination "
        f"where vintage_year in ({years}) group by 1"
    )
    b_perf = by_vintage(
        f"select {ID_VINTAGE} as v, count(*), sum(current_upb) from {bronze_perf} "
        f"where {ID_VINTAGE} in ({years}) group by 1"
    )
    s_perf = by_vintage(
        "select vintage_year, count(*), sum(current_upb), count(distinct loan_id), "
        "count(*) filter (where remaining_months_derived) from silver.int_freddie__loan_months "
        f"where vintage_year in ({years}) group by 1"
    )
    no_months = by_vintage(
        "select o.vintage_year, count(*) from silver.stg_freddie__origination o "
        f"where o.vintage_year in ({years}) and not exists "
        "(select 1 from silver.stg_freddie__performance m where m.loan_id = o.loan_id) group by 1"
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


def totals(watch: dict[str, Any]) -> dict[str, int]:
    """Watch counts summed over vintages, for the run summary."""
    out: dict[str, int] = {}
    for counts in watch.values():
        for k, n in counts.items():
            out[k] = out.get(k, 0) + n
    return out
