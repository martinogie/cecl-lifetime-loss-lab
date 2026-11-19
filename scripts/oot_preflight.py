"""Pre-flight checks for the out-of-time test: counts and dates only, nothing scored.

    CECL_ENV=dev python -m scripts.oot_preflight --warehouse <sql warehouse id>

Read-only SQL on the dev tables (SQL warehouse, no job compute). No model is loaded and no outcome is
counted: only row counts, loan counts and the first and last months, so the test months stay unseen.
Checks that:
- the loan panel runs to the end of the test window (June 2026) and has loans alive on 1 July 2022;
- the test months are only in the holdout table (none in the training/validation transition table),
  and the holdout covers exactly July 2022 to June 2026;
- every economic series the projection reads runs to May 2026 (each month's inputs use the month
  before), for every state.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from cecl import environment
from cecl.models import oot

PROFILE = "cecl"
STATE_VARS = ("hpi", "unemployment_rate")
NATIONAL_VARS = ("mortgage_rate_30y",)


def last_input_month() -> str:
    """The last month the projection's inputs read: the month before the test's last month."""
    y, m = int(oot.TEST_END[:4]), int(oot.TEST_END[4:])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y}{m:02d}"


def queries(gold: str, silver: str) -> dict[str, str]:
    s, e = oot.TEST_START, oot.TEST_END
    return {
        "panel_last_month": f"select max(period_ym) as v from {gold}.loan_panel",
        "loans_alive_at_start": f"select count(distinct loan_id) as v from {gold}.loan_panel "
        f"where period_ym = '{s}' and status <> 'censored'",
        "test_rows_in_transitions": f"select count(*) as v from {gold}.transitions where period_ym >= '{s}'",
        "holdout_rows": f"select count(*) as v from {gold}.transitions_holdout",
        "holdout_first_month": f"select min(period_ym) as v from {gold}.transitions_holdout",
        "holdout_last_month": f"select max(period_ym) as v from {gold}.transitions_holdout",
        "holdout_outside_window": f"select count(*) as v from {gold}.transitions_holdout "
        f"where period_ym < '{s}' or period_ym > '{e}'",
        **{
            f"national_{v}_last": f"select date_format(max(month), 'yyyyMM') as v from {silver}.macro_national_monthly "
            f"where variable = '{v}'"
            for v in NATIONAL_VARS
        },  # fmt: skip
        **{
            f"states_short_{v}": f"select count(*) as v from (select state, max(month) as m from {silver}.macro_state_monthly "
            f"where variable = '{v}' group by state) where date_format(m, 'yyyyMM') < '{last_input_month()}'"
            for v in STATE_VARS
        },  # fmt: skip
        **{
            f"states_with_{v}": f"select count(distinct state) as v from {silver}.macro_state_monthly where variable = '{v}'"
            for v in STATE_VARS
        },  # fmt: skip
    }


def problems(facts: dict[str, Any]) -> list[str]:
    """What stops the test, from the facts (all counts or YYYYMM strings)."""
    out = []
    need = last_input_month()
    if str(facts["panel_last_month"]) < oot.TEST_END:
        out.append(f"the panel ends {facts['panel_last_month']}, before {oot.TEST_END}")
    if int(facts["loans_alive_at_start"]) == 0:
        out.append(f"no loans alive in {oot.TEST_START}")
    if int(facts["test_rows_in_transitions"]) != 0:
        out.append(
            f"{facts['test_rows_in_transitions']} test-month rows in the training/validation transition table"
        )
    if (str(facts["holdout_first_month"]), str(facts["holdout_last_month"])) != (
        oot.TEST_START,
        oot.TEST_END,
    ):
        out.append(
            f"holdout covers {facts['holdout_first_month']}-{facts['holdout_last_month']}, not {oot.TEST_START}-{oot.TEST_END}"
        )
    if int(facts["holdout_outside_window"]) != 0:
        out.append(f"{facts['holdout_outside_window']} holdout rows outside the test window")
    for v in NATIONAL_VARS:
        if str(facts[f"national_{v}_last"]) < need:
            out.append(f"national {v} ends {facts[f'national_{v}_last']}, before {need}")
    for v in STATE_VARS:
        if int(facts[f"states_short_{v}"]) != 0:
            out.append(f"{facts[f'states_short_{v}']} states' {v} end before {need}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--warehouse", required=True, help="SQL warehouse id")
    args = parser.parse_args(argv)
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=PROFILE)
    facts: dict[str, Any] = {}
    for name, sql in queries(
        environment.databricks_schema("gold"), environment.databricks_schema("silver")
    ).items():
        r = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse, statement=sql, wait_timeout="50s"
        )
        while r.status and r.status.state and r.status.state.value in ("PENDING", "RUNNING"):
            r = w.statement_execution.get_statement(r.statement_id or "")
        if not (r.status and r.status.state and r.status.state.value == "SUCCEEDED"):
            raise SystemExit(f"{name}: query failed: {r.status}")
        rows: list[list[Any]] = list(r.result.data_array or []) if r.result else []
        facts[name] = rows[0][0] if rows else None
        print(f"{name}: {facts[name]}")
    found = problems(facts)
    print(
        "\npre-flight: "
        + ("PASS (counts and dates only; nothing scored)" if not found else "FAIL\n- " + "\n- ".join(found))
    )
    return 0 if not found else 1


if __name__ == "__main__":
    sys.exit(main())
