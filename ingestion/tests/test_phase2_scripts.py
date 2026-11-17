"""The out-of-time test's guard reads ADR-009 itself (Phase 2 step 7)."""

from __future__ import annotations

from pathlib import Path

from scripts import run_oot_test

ROOT = Path(__file__).resolve().parents[2]


def test_adr_009_is_accepted_with_martins_decision() -> None:
    assert run_oot_test.adr_accepted(
        (ROOT / "docs" / "adr" / "009-model-selection-rule.md").read_text(encoding="utf-8")
    )


def test_guard_refuses_a_draft() -> None:
    draft = (
        "- **Status:** proposed, 16 November 2026\n\n## Decision\n\n*Pending: Martins writes this section*\n"
    )
    assert not run_oot_test.adr_accepted(draft)
    no_decision = "- **Status:** accepted\n\n## Decision\n\n(empty)\n"
    assert not run_oot_test.adr_accepted(no_decision)
