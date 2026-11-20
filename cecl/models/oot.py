"""The out-of-time test for the multi-state design (ADR-009 E, Amendments 1 E and 2): the pieces that
decide and guard it, kept here (not in the notebook) so they are tested on made-up data.

- The verdict (Amendment 2 (a) and (b)): the test passes when every judged default cell of the full
  multi-state projection from 1 July 2022 (by year and by vintage, cells with at least 200 actual
  defaults) is inside 0.80-1.25. Payoff cells are reported; a payoff cell outside the bar is recorded
  under limitations log L-028 and does not fail the test.
- The launcher's guards (Martins, 18 November 2026): the test is not started until his written
  expectation is marked final and the pre-registered result statement is marked approved, the locked
  model set is logged as a `selection` run and registered as @challenger, and the COVID sensitivity
  has been recorded.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from cecl import features as fx
from cecl.models import multistate

TEST_START, TEST_END = next((first, last) for name, first, last in fx.SPLITS if name == fx.TEST)
TEST_MONTHS = multistate.months_between(TEST_START, TEST_END)  # 48: July 2022 to June 2026
STEPS = ("current", "30", "60")  # the three transition models of a combination, by starting state

# Markers the launcher looks for (a line starting with exactly this text); Martins sets them himself.
EXPECTATIONS_FINAL = "- **Status:** final, confirmed by Martins"
STATEMENT_APPROVED = "- **Status:** approved by Martins"


def verdict(check: multistate.Check) -> dict[str, Any]:
    """Amendment 2 (a) and (b): pass or fail on the judged default cells only."""
    judged = [c for c in check.cells if c.applies]
    default_fail = [asdict(c) for c in judged if c.event == "default" and not c.passes]
    payoff_fail = [asdict(c) for c in judged if c.event == "prepaid" and not c.passes]
    return {
        "passes": not default_fail,
        "judged_default_cells": sum(1 for c in judged if c.event == "default"),
        "judged_payoff_cells": sum(1 for c in judged if c.event == "prepaid"),
        "failing_default_cells": default_fail,
        "failing_payoff_cells_recorded_under_L028": payoff_fail,
        "rule": "ADR-009 Amendment 2 (a): every judged default cell inside 0.80-1.25; (b) payoff cells reported, "
        "failures recorded under L-028",
    }


def has_marker(text: str, marker: str) -> bool:
    """True when a line of `text` starts with `marker` (Martins's own status line)."""
    return any(line.strip().startswith(marker) for line in text.splitlines())


def locked_set(params: dict[str, str]) -> dict[str, str]:
    """The locked combination {step: run_id} from a selection run's parameters (run_current, run_30,
    run_60); refuses if a step is missing."""
    runs = {s: params.get(f"run_{s}", "") for s in STEPS}
    missing = [s for s, r in runs.items() if not r]
    if missing:
        raise ValueError(f"the selection run names no model for step(s) {missing}")
    return runs


def covid_sensitivity_missing(runs: list[dict[str, str]], family: str) -> list[str]:
    """Steps with no COVID-sensitivity training run of the chosen family. `runs` holds each run's tags
    (`transition`, `family`, `sensitivity`)."""
    have = {
        r.get("transition")
        for r in runs
        if r.get("family") == family and r.get("sensitivity") == "covid_in_training"
    }
    return [s for s in STEPS if s not in have]


def challenger_mismatches(locked: dict[str, str], registered: dict[str, str | None]) -> list[str]:
    """Steps whose @challenger version does not point to the locked run (or has none)."""
    return [s for s in STEPS if registered.get(s) != locked[s]]


def registered_name(step: str) -> str:
    """Unity Catalog model name (in the environment's `ml` schema) for one transition model."""
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}")
    return f"pd_transition_{step}"


def start_actuals_tie_out(start_ids: Any, actual_ids: Any) -> tuple[int, int]:
    """(start loans with no outcome row, outcome rows not in the start table). The first must be 0.
    The second is allowed: a loan whose start-month row is censored (it left the data that month) has
    an outcome row but is not alive, so it is not in the start table (15 such loans in July 2022, 3 in
    January 2017; found by the first launch, 18 November 2026, which stopped here before scoring)."""
    start, actual = set(start_ids), set(actual_ids)
    return len(start - actual), len(actual - start)
