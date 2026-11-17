"""Build the model's feature table from the panel, silver and the economic data (Phase 2 step 2).

The same SQL runs in two places (Phase 2 plan, amended 16 November 2026):
- **Databricks** (Spark SQL, `databricks/notebooks/40_features.py`): the full build;
- **the PC** (DuckDB): the reference, for the row-by-row comparison of a few vintages.
Only the relation names and a handful of functions differ (`DIALECTS`), so both engines keep the
same rows, compute the same values and give the same weights.

For one vintage:
1. Every panel row (loan-month t) gets its features from month t-1: the loan's previous panel row
   (balance, rate, lateness, forbearance flag; origination values for its first month), the state
   house-price index and unemployment rate, and the market mortgage rate (cecl/features.py).
2. Censored months are left out (their outcome is unknown); the outcome is no event, default or
   prepayment.
3. **Training table:** every event kept; about 1 in 25 no-event months kept by a fixed hash, weighted
   so that, per stratum (vintage x observation year), the weighted no-event count equals the panel's
   exactly. Small strata (under 500 no-event months) are kept whole.
4. **Horizon table** (validation and test months only): every month of 1 loan in 10 (fixed hash),
   weight 1, so AUC at 12, 24 and 36 months can follow each loan's path month by month.
5. Tie-outs (control C02): defaults, prepayments and weighted no-event months equal the panel's;
   panel rows = events + no-event + censored. A mismatch stops the build.
6. The test months go to separate holdout tables, read only by the test command (ADR-009 E).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from cecl import features as fx
from cecl.controls import TieOut

SMALL_STRATUM = 500  # no-event months; below this a stratum is kept whole (weight 1)
MEMORY_LIMIT = "1500MB"  # DuckDB's cap on the PC
HORIZON_EVERY = 10  # the horizon table keeps every month of 1 loan in 10
HORIZON_SPLITS = (fx.VALIDATE, fx.TEST)

# The few places where DuckDB and Spark SQL differ. Everything else in the query is shared.
DIALECTS: dict[str, dict[str, str]] = {
    "duckdb": {
        "to_month": "strptime({c}, '%Y%m')::date",
        "add_months": "cast({d} + interval ({n}) month as date)",
        # first 8 hex digits of the MD5, as an integer: the same number in both engines
        "hex8": "cast('0x' || substr(md5({s}), 1, 8) as bigint)",
        "star_except": "exclude",
    },
    "spark": {
        "to_month": "to_date({c}, 'yyyyMM')",
        "add_months": "add_months({d}, {n})",
        "hex8": "cast(conv(substr(md5({s}), 1, 8), 16, 10) as bigint)",
        "star_except": "except",
    },
}


def local_relations(root: Path, vintage: int) -> dict[str, str]:
    """The inputs on the PC: the panel and silver macro Parquet, and dbt's silver views."""
    local = Path(root) / "local"
    panel = (local / "panel" / f"vintage={int(vintage)}" / "panel.parquet").as_posix()
    macro = (local / "silver" / "macro").as_posix()
    return {
        "panel": f"read_parquet('{panel}')",
        "silver": "silver",
        "state": f"read_parquet('{macro}/state_monthly.parquet')",
        "national": f"read_parquet('{macro}/national_monthly.parquet')",
    }


def databricks_relations(schema: Any, vintage: int) -> dict[str, str]:
    """The same inputs on Databricks; `schema` is cecl.environment.databricks_schema."""
    return {
        "panel": f"(select * from {schema('gold')}.loan_panel where vintage_year = {int(vintage)})",
        "silver": schema("silver"),
        "state": f"{schema('silver')}.macro_state_monthly",
        "national": f"{schema('silver')}.macro_national_monthly",
    }


def sample_condition(dialect: str, seed: str = fx.SAMPLE_SEED, every: int = fx.SAMPLE_EVERY) -> str:
    key = f"loan_id || '|' || period_ym || '|{seed}'"
    return f"({DIALECTS[dialect]['hex8'].format(s=key)} % {every}) = 0"


def horizon_condition(dialect: str, seed: str = fx.SAMPLE_SEED, every: int = HORIZON_EVERY) -> str:
    key = f"loan_id || '|horizon|{seed}'"  # by loan only, so a chosen loan keeps all its months
    return f"({DIALECTS[dialect]['hex8'].format(s=key)} % {every}) = 0"


def _rows_ctes(vintage: int, rel: dict[str, str], dialect: str) -> str:
    """Every non-censored loan-month of the vintage with its features (shared by both tables)."""
    d = DIALECTS[dialect]
    v = int(vintage)

    def prev(col: str, n: int = -1) -> str:
        return d["add_months"].format(d=col, n=n)

    return f"""
with p as (
    select loan_id, vintage_year, period_ym, loan_age, cast(current_upb as double) as upb, status,
           effective_months_late, flagged_pause, {d["to_month"].format(c="period_ym")} as month
    from {rel["panel"]}
),
m as (
    select loan_id, period_ym, cast(current_interest_rate as double) as rate, eltv
    from {rel["silver"]}.int_freddie__loan_months where vintage_year = {v}
),
o as (
    select loan_id, credit_score, orig_ltv, orig_dti, mi_pct, cast(orig_upb as double) as orig_upb,
           cast(orig_interest_rate as double) as orig_rate, term_over_360, property_state,
           occupancy_status, loan_purpose, relief_refinance,
           {prev(d["to_month"].format(c="first_payment_ym"))} as orig_month  -- the month before the first payment
    from {rel["silver"]}.stg_freddie__origination where vintage_year = {v}
),
lagged as (
    select p.*, m.eltv,
        lag(p.upb) over w as upb_prev,
        lag(m.rate) over w as rate_prev,
        lag(p.effective_months_late) over w as late_prev,
        lag(p.flagged_pause) over w as pause_prev,
        lag(m.eltv) over w as eltv_prev,
        coalesce(sum(case when p.effective_months_late >= 1 then 1 else 0 end) over (
            partition by p.loan_id order by p.period_ym rows between 12 preceding and 1 preceding), 0)
            as late_months_12
    from p left join m on m.loan_id = p.loan_id and m.period_ym = p.period_ym
    window w as (partition by p.loan_id order by p.period_ym)
),
hpi as (select state, month, value from {rel["state"]} where variable = 'hpi'),
ur as (select state, month, value from {rel["state"]} where variable = 'unemployment_rate'),
mkt as (select month, value from {rel["national"]} where variable = 'mortgage_rate_30y'),
rows as (
    select
        l.loan_id, l.vintage_year, l.period_ym, l.status,
        {fx.split_sql("l.period_ym")} as split,
        case l.status when 'defaulted' then {fx.DEFAULT} when 'prepaid' then {fx.PREPAY} else {fx.NONE} end
            as outcome,
        substr(l.period_ym, 1, 4) as obs_year,
        l.loan_age,
        cast(o.credit_score as double) as credit_score,
        100.0 * coalesce(l.upb_prev, o.orig_upb)
            / (o.orig_upb / (o.orig_ltv / 100.0) * (h1.value / h0.value)) as current_ltv,
        cast(o.orig_dti as double) as orig_dti,
        coalesce(l.rate_prev, o.orig_rate) - mk.value as rate_incentive,
        cast(coalesce(l.late_prev, 0) as double) as months_late_prev,
        cast(l.late_months_12 as double) as late_months_12,
        u1.value as unemployment,
        u1.value - u13.value as unemployment_change_12,
        case when coalesce(l.pause_prev, false) then 1.0 else 0.0 end as forbearance_prev,
        cast(o.mi_pct as double) as mi_pct,
        o.orig_upb,
        case when o.term_over_360 then 1.0 else 0.0 end as term_over_360,
        case when o.loan_purpose = 'C' then 1.0 else 0.0 end as purpose_cashout,
        case when o.loan_purpose in ('N', 'R') then 1.0 else 0.0 end as purpose_refi,
        case when o.occupancy_status = 'I' then 1.0 else 0.0 end as occupancy_investor,
        case when o.occupancy_status = 'S' then 1.0 else 0.0 end as occupancy_second,
        case when o.relief_refinance then 1.0 else 0.0 end as relief_refinance,
        cast(month(l.month) as double) as calendar_month,
        o.property_state,
        cast(l.eltv_prev as double) as eltv_prev
    from lagged l
    join o on o.loan_id = l.loan_id
    left join hpi h1 on h1.state = o.property_state and h1.month = {prev("l.month")}
    left join hpi h0 on h0.state = o.property_state and h0.month = o.orig_month
    left join ur u1 on u1.state = o.property_state and u1.month = {prev("l.month")}
    left join ur u13 on u13.state = o.property_state and u13.month = {prev("l.month", -13)}
    left join mkt mk on mk.month = {prev("l.month")}
    where l.status <> '{fx.EXCLUDED_STATUS}'
)"""


def training_sql(vintage: int, rel: dict[str, str], dialect: str) -> str:
    """The sampled, weighted training table for one vintage (all splits)."""
    ex = DIALECTS[dialect]["star_except"]
    return (
        _rows_ctes(vintage, rel, dialect)
        + f""",
flagged as (select r.*, {sample_condition(dialect)} as sampled from rows r),
strata_all as (
    select obs_year, count(*) filter (where outcome = {fx.NONE}) as n_all from rows group by obs_year
),
kept as (
    select f.*, s.n_all from flagged f join strata_all s on s.obs_year = f.obs_year
    where f.outcome <> {fx.NONE} or f.sampled or s.n_all < {SMALL_STRATUM}
),
strata_kept as (
    select obs_year, count(*) filter (where outcome = {fx.NONE}) as n_kept from kept group by obs_year
)
select k.* {ex} (sampled, n_all, status, obs_year),
       case when k.outcome = {fx.NONE} then cast(k.n_all as double) / sk.n_kept else 1.0 end as weight
from kept k join strata_kept sk on sk.obs_year = k.obs_year
"""
    )


def horizon_sql(vintage: int, rel: dict[str, str], dialect: str) -> str:
    """Every month of 1 loan in 10, validation and test months only (weight 1)."""
    ex = DIALECTS[dialect]["star_except"]
    splits = ", ".join(f"'{s}'" for s in HORIZON_SPLITS)
    return (
        _rows_ctes(vintage, rel, dialect)
        + f"""
select r.* {ex} (status, obs_year), 1.0 as weight
from rows r where r.split in ({splits}) and {horizon_condition(dialect)}
"""
    )


def panel_counts_sql(panel_rel: str) -> str:
    return (
        f"select count(*) filter (where status = 'defaulted'), count(*) filter (where status = 'prepaid'), "
        f"count(*) filter (where status in ('current', 'delinquent')), "
        f"count(*) filter (where status = '{fx.EXCLUDED_STATUS}'), count(*) from {panel_rel}"
    )


def totals_sql(relation: str) -> str:
    """The training table's totals that the tie-outs compare with the panel (any engine)."""
    return (
        f"select count(*) filter (where outcome = {fx.DEFAULT}), count(*) filter (where outcome = {fx.PREPAY}), "
        f"coalesce(sum(case when outcome = {fx.NONE} then weight else 0 end), 0) from {relation}"
    )


def tie_outs(
    vintage: int, panel: Sequence[int], got_default: int, got_prepay: int, weighted_none: float
) -> list[TieOut]:
    """The four tie-outs of one vintage: the panel's counts against the training table's totals."""
    defaults, prepays, none_, censored, total = (int(x) for x in panel)
    return [
        TieOut(f"vintage {vintage}: defaults, panel vs features", defaults, int(got_default)),
        TieOut(f"vintage {vintage}: prepayments, panel vs features", prepays, int(got_prepay)),
        TieOut(f"vintage {vintage}: no-event months, panel vs weighted features", none_, round(weighted_none)),
        TieOut(f"vintage {vintage}: panel rows = events + no-event + censored", total,
               defaults + prepays + none_ + censored),
    ]  # fmt: skip


# ------------------------------------------------------------------------------- the PC's build


def feature_dir(root: Path, split: str, table: str = "features") -> Path:
    """local/features (train, validate, covid) or local/features_holdout (test); horizon alike."""
    base = f"{table}_holdout" if split in fx.HOLDOUT else table
    return Path(root) / "local" / base / f"split={split}"


@dataclass
class VintageResult:
    vintage: int
    rows_by_split: dict[str, int] = field(default_factory=dict)
    horizon_rows: int = 0
    tie_outs: list[TieOut] = field(default_factory=list)
    censored: int = 0


def build_vintage(con: Any, root: Path, vintage: int) -> VintageResult:
    """Build, tie out and write one vintage on the PC (DuckDB)."""
    rel = local_relations(root, vintage)
    table: pa.Table = con.sql(
        training_sql(vintage, rel, "duckdb") + " order by loan_id, period_ym"
    ).to_arrow_table()
    panel = con.execute(panel_counts_sql(rel["panel"])).fetchall()[0]
    result = VintageResult(vintage, censored=int(panel[3]))
    outcome, weight = table.column("outcome").to_pylist(), table.column("weight").to_pylist()
    result.tie_outs = tie_outs(
        vintage,
        panel,
        sum(1 for o in outcome if o == fx.DEFAULT),
        sum(1 for o in outcome if o == fx.PREPAY),
        sum(w for o, w in zip(outcome, weight, strict=True) if o == fx.NONE),
    )
    for split in sorted(set(table.column("split").to_pylist())):
        part = table.filter(pc.equal(table.column("split"), split))
        _write(part, feature_dir(root, split) / f"vintage={vintage}.parquet")
        result.rows_by_split[split] = part.num_rows
    horizon: pa.Table = con.sql(
        horizon_sql(vintage, rel, "duckdb") + " order by loan_id, period_ym"
    ).to_arrow_table()
    for split in sorted(set(horizon.column("split").to_pylist())):
        part = horizon.filter(pc.equal(horizon.column("split"), split))
        _write(part, feature_dir(root, split, "features_horizon") / f"vintage={vintage}.parquet")
        result.horizon_rows += part.num_rows
    return result


def _write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


# ------------------------------------------------------------------------------- whole-table checks


def contract_sql(relation: str) -> str:
    """Rows outside each range, and the share missing, over a feature table (any engine)."""
    checks = []
    for name in fx.FEATURE_NAMES:
        lo, hi = fx.RANGES.get(name, (None, None))
        parts = [f"{name} < {lo}" if lo is not None else "", f"{name} > {hi}" if hi is not None else ""]
        bad = " or ".join(c for c in parts if c)
        checks.append(f"count(*) filter (where {bad or 'false'}) as {name}__out_of_range")
        checks.append(f"avg(case when {name} is null then 1.0 else 0.0 end) as {name}__missing")
    return f"select count(*) as rows, {', '.join(checks)} from {relation}"


def parquet_relation(paths: Sequence[str]) -> str:
    return "read_parquet([" + ", ".join(f"'{p}'" for p in paths) + "])"


def contract_problems(result: dict[str, Any]) -> list[str]:
    problems = []
    for name in fx.FEATURE_NAMES:
        if result[f"{name}__out_of_range"]:
            problems.append(f"{name}: {result[f'{name}__out_of_range']:,} rows outside {fx.RANGES[name]}")
        if result[f"{name}__missing"] > fx.max_missing_share(name):
            problems.append(
                f"{name}: {result[f'{name}__missing']:.1%} missing (limit {fx.max_missing_share(name):.0%})"
            )
    return problems


def eltv_check_sql(relation: str) -> str:
    """Our current LTV against Freddie's estimated LTV (eltv) for the same month, where both exist."""
    return (
        "select count(*), percentile_cont(0.5) within group (order by abs(current_ltv - eltv_prev)), "
        "corr(current_ltv, eltv_prev), avg(case when abs(current_ltv - eltv_prev) <= 10 then 1.0 else 0.0 end) "
        f"from {relation} where current_ltv is not null and eltv_prev is not null"
    )
