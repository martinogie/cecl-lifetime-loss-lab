"""The out-of-time test's guard reads ADR-009 itself (Phase 2 step 7)."""

from __future__ import annotations

from pathlib import Path

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
