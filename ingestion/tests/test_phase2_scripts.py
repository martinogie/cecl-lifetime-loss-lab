"""The out-of-time test's guard reads ADR-009 itself (Phase 2 step 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_oot_test

ROOT = Path(__file__).resolve().parents[2]


def test_adr_009_with_a_pending_amendment_blocks_the_test() -> None:
    text = (ROOT / "docs" / "adr" / "009-model-selection-rule.md").read_text(encoding="utf-8")
    pending = "Status of this amendment:** proposed" in text
    assert run_oot_test.adr_accepted(text) is (not pending)


def test_accepted_amendment_unblocks() -> None:
    text = (
        "- **Status:** accepted\n**Decision (Martins, 16 November 2026).**\n"
        "- **Status of this amendment:** accepted, 18 November 2026\n"
    )
    assert run_oot_test.adr_accepted(text)
    assert not run_oot_test.adr_accepted(text.replace("amendment:** accepted", "amendment:** proposed"))


def test_guard_refuses_a_draft() -> None:
    draft = (
        "- **Status:** proposed, 16 November 2026\n\n## Decision\n\n*Pending: Martins writes this section*\n"
    )
    assert not run_oot_test.adr_accepted(draft)
    no_decision = "- **Status:** accepted\n\n## Decision\n\n(empty)\n"
    assert not run_oot_test.adr_accepted(no_decision)


def test_transition_plan_runs_nine_models_shortest_first() -> None:
    from scripts import run_sequence

    plan = run_sequence.PLANS["transitions"]
    assert len(plan) == 9 and all(job == run_sequence.TRAIN for job, _ in plan)
    order = [(p["start_state"], p["family"]) for _, p in plan]
    assert order[:3] == [("60", "logistic"), ("60", "lightgbm"), ("60", "ebm")]
    assert order[-1] == ("current", "ebm") and plan[-1][1]["ebm_sample_every"] == "4"
    assert "ebm_sample_every" not in plan[2][1]  # T-60's EBM uses every row


def test_sequence_waits_out_a_blocked_window() -> None:
    from datetime import datetime

    from cecl import scheduling
    from scripts import run_sequence

    naps: list[int] = []
    blocked = datetime(2026, 11, 18, 3, 0, tzinfo=scheduling.TZ)  # 03:00, the reboot window
    free = datetime(2026, 11, 18, 10, 0, tzinfo=scheduling.TZ)
    assert run_sequence.wait_for_window(free, sleep=naps.append) == 0 and naps == []
    # from a blocked moment it naps until the clock (here: the real one) is outside the window
    real_now_blocked = scheduling.blocked_reason(datetime.now(scheduling.TZ)) is not None
    if not real_now_blocked:
        assert run_sequence.wait_for_window(blocked, sleep=naps.append) == 1 and naps == [60]


# ---------------------------------------------------------------------------- multi-state test guards
def test_launcher_document_guards() -> None:
    from cecl.models import oot

    adr = (ROOT / "docs/adr/009-model-selection-rule.md").read_text(encoding="utf-8")
    final, approved = f"{oot.EXPECTATIONS_FINAL}\n", f"{oot.STATEMENT_APPROVED}\n"
    assert run_oot_test.document_problems(adr, final, approved) == []
    found = run_oot_test.document_problems(adr, "- **Status:** draft", "- **Status:** draft")
    assert len(found) == 2 and "expectations" in found[0] and "result_statement" in found[1]


def test_launcher_passes_the_other_models_for_information() -> None:
    locked = {"current": "L0", "30": "L1", "60": "L2"}
    params = {"run_current": "L0", "info_current_logistic": "L0", "info_current_lightgbm": "G0",
              "info_30_ebm": "E1", "info_60_logistic": "L2"}  # fmt: skip
    combos, models = run_oot_test.info_from_selection(params, locked)
    assert models == {"current": {"lightgbm": "G0"}, "30": {"ebm": "E1"}}
    assert combos == {"info_lightgbm_current": {"current": "G0", "30": "L1", "60": "L2"}}


def test_lock_model_set_reads_the_approved_set() -> None:
    from types import SimpleNamespace

    from scripts import lock_model_set

    assert lock_model_set.parse_expect("current=logistic,30=logistic,60=ebm") == {
        "current": "logistic",
        "30": "logistic",
        "60": "ebm",
    }
    for bad in ("current=logistic,30=logistic", "current=logistic,30=logistic,60=xgb"):
        with pytest.raises(ValueError):
            lock_model_set.parse_expect(bad)

    def run(step: str, fam: str, t: int, sens: str = "none") -> SimpleNamespace:
        return SimpleNamespace(data=SimpleNamespace(tags={"transition": step, "family": fam, "sensitivity": sens}),
                               info=SimpleNamespace(start_time=t, run_id=f"{step}-{fam}-{t}"))  # fmt: skip

    found = lock_model_set.newest_by_step_family(
        [run("30", "ebm", 1), run("30", "ebm", 5), run("30", "ebm", 9, "covid_in_training")]
    )
    assert found[("30", "ebm")].info.run_id == "30-ebm-5"  # newest, sensitivity runs ignored


def test_preflight_checks_counts_and_dates() -> None:
    from scripts import oot_preflight

    assert oot_preflight.last_input_month() == "202605"
    good = {"panel_last_month": "202606", "loans_alive_at_start": 300000, "test_rows_in_transitions": 0,
            "holdout_first_month": "202207", "holdout_last_month": "202606", "holdout_outside_window": 0,
            "national_mortgage_rate_30y_last": "202606", "states_short_hpi": 0, "states_short_unemployment_rate": 0}  # fmt: skip
    assert oot_preflight.problems(good) == []
    bad = {**good, "panel_last_month": "202603", "test_rows_in_transitions": 12, "states_short_hpi": 3}
    assert len(oot_preflight.problems(bad)) == 3
    q = oot_preflight.queries("g", "s")
    assert all("model" not in sql and "outcome" not in sql for sql in q.values())  # counts and dates only


def test_v2_holdout_guards_on_the_documents() -> None:
    from cecl.models import oot
    from scripts import run_v2_holdout as h

    adr = (ROOT / "docs/adr/009-model-selection-rule.md").read_text(encoding="utf-8")
    final = f"# x\n\n{oot.EXPECTATIONS_FINAL} (19 November 2026)\n"
    assert h.document_problems(adr, final) == []
    assert h.document_problems(adr, "- **Status:** draft\n")  # expectations not final
    no_lock = adr.replace(h.LOCK, "the holdout rules are open")
    assert h.document_problems(no_lock, final)
    assert h.document_problems(adr.split("## Amendment 5")[0], final)  # no Amendment 5


def test_v2_holdout_needs_a_passing_downturn_check_for_the_same_runs() -> None:
    import json
    from types import SimpleNamespace as NS

    from scripts import run_v2_holdout as h

    runs = {"current": "a", "30": "b", "60": "c"}

    def run(rid: str, passes: float, **tags: str) -> NS:
        return NS(
            info=NS(run_id=rid),
            data=NS(params={"runs": json.dumps(runs)}, metrics={"passes": passes}, tags=tags),
        )

    assert h.downturn_passed(run("k", 1.0), None, runs)
    failed = run("k", 0.0)
    assert not h.downturn_passed(failed, None, runs)
    assert h.downturn_passed(failed, run("r", 1.0, source_run="k"), runs)
    assert not h.downturn_passed(failed, run("r", 1.0, source_run="other"), runs)
    assert not h.downturn_passed(run("k", 1.0), None, runs | {"60": "z"})  # a different model set


def test_v2_holdout_verdict_needs_cells_and_total() -> None:
    from cecl.models import multistate
    from cecl.models import v2_pipeline as v2

    def cell(event: str, ratio: float, actual: float = 500.0) -> multistate.Cell:
        return multistate.Cell(
            event,
            "year",
            "2025",
            actual,
            actual * ratio,
            ratio,
            actual >= 200,
            0.80 <= ratio <= 1.25 or actual < 200,
        )

    ok = multistate.Check(cells=[cell("default", 1.0), cell("prepaid", 0.5)],
                          totals={"default_actual": 1000.0, "default_projected": 1050.0})  # fmt: skip
    out = v2.holdout_verdict(ok)
    assert out["passes"] and len(out["failing_payoff_cells_recorded_under_L028"]) == 1  # payoffs never fail
    bad_total = multistate.Check(
        cells=[cell("default", 1.0)], totals={"default_actual": 1000.0, "default_projected": 1300.0}
    )
    assert not v2.holdout_verdict(bad_total)["passes"]
    bad_cell = multistate.Check(
        cells=[cell("default", 1.3)], totals={"default_actual": 1000.0, "default_projected": 1000.0}
    )
    assert not v2.holdout_verdict(bad_cell)["passes"]
    small = multistate.Check(
        cells=[cell("default", 2.0, actual=50.0)],
        totals={"default_actual": 1000.0, "default_projected": 1000.0},
    )
    assert v2.holdout_verdict(small)["passes"]  # a cell under 200 actual defaults is not judged
    assert (v2.HOLDOUT_START, v2.HOLDOUT_END, v2.HOLDOUT_MONTHS) == ("202407", "202606", 24)


def test_build_stress_quarterly_and_weighting() -> None:
    """Phase 5: the 9-quarter loss rates and the scenario weighting, on made-up aggregates."""
    pytest.importorskip("pandas")
    from scripts import build_stress as bs

    agg = {"balance": 1000.0, "reserve": 50.0, "lifetime_pd": 0.03, "q_el": [1.0] * 12,
           "q_defaults": [2.0] * 12, "q_bal": [2700.0] * 12,
           "paths": {"quarter_end": [f"q{i}" for i in range(12)], "unemployment": [5.0] * 12,
                     "hpi_change": [0.0] * 12, "mortgage_rate": [6.0] * 12}}  # fmt: skip
    q = bs.quarterly(agg)
    assert q["loss_9q"] == pytest.approx(9.0)
    assert q["loss_rate_9q_of_start_balance"] == pytest.approx(0.009)
    assert q["average_balance_9q"] == pytest.approx(
        (1000.0 + 9 * 900.0) / 10
    )  # start and 9 quarterly averages
    assert len(q["quarters"]) == 12 and q["quarters"][0]["loss_rate_of_start_balance"] == pytest.approx(0.001)
    by_s = {
        "baseline": {"reserve": 10.0},
        "adverse": {"reserve": 20.0},
        "severely_adverse": {"reserve": 100.0},
    }
    assert bs.weighted(by_s) == pytest.approx(6.0 + 6.0 + 10.0)
    assert bs.weighted(by_s, (0.4, 0.4, 0.2)) == pytest.approx(4.0 + 8.0 + 20.0)
    assert bs.cache_name("", "baseline", 0.05).name == "agg_202606_202606_baseline__floor05.json"
    assert len(bs.runs()) == 3 + 6 + 10 + 7 * 3
