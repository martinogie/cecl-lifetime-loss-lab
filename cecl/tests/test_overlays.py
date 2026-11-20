"""ADR-006 overlay rules, on made-up overlays."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from cecl import overlays as ov

BASE = ov.Overlay(
    overlay_id="OV-1",
    reserve_ym="202606",
    factor="Q7",
    segment="FL loans",
    amount=4.0,
    method="numbers",
    evidence="re-run with FL house prices -5%",
    maker="Maker",
    maker_date="2026-07-10",
    checker="Checker",
    checker_date="2026-07-12",
)


def test_quarter_end() -> None:
    assert ov.quarter_end("202606") == date(2026, 6, 30)
    assert ov.quarter_end("202604") == date(2026, 6, 30)
    assert ov.quarter_end("202512") == date(2025, 12, 31)
    assert ov.quarter_end("202601") == date(2026, 3, 31)


def test_a_valid_approved_overlay_is_booked() -> None:
    d = ov.check(BASE, modelled=100.0)
    assert d.status == "booked" and not d.problems
    assert d.share_of_modelled == pytest.approx(0.04)
    assert ov.size_by_rerun(100.0, 104.0) == pytest.approx(4.0)


def test_not_applicable_or_unknown_factor_is_rejected() -> None:
    assert ov.check(replace(BASE, factor="Q4"), 100.0).status == "rejected"
    assert ov.check(replace(BASE, factor="Q10"), 100.0).status == "rejected"


def test_cap_per_overlay_needs_a_written_exception() -> None:
    big = replace(BASE, amount=6.0)
    assert ov.check(big, 100.0).status == "rejected"
    d = ov.check(replace(big, cap_exception="L-038: gap above the cap, see sizing"), 100.0)
    assert d.status == "booked" and d.flags  # booked, flagged in the memo


def test_percentage_needs_justification_and_decrease_needs_numbers_and_checker() -> None:
    assert ov.check(replace(BASE, method="percentage"), 100.0).status == "rejected"
    assert ov.check(replace(BASE, method="percentage", justification="why"), 100.0).status == "booked"
    assert (
        ov.check(replace(BASE, amount=-3.0, method="percentage", justification="why"), 100.0).status
        == "rejected"
    )
    assert ov.check(replace(BASE, amount=-3.0, checker_date=""), 100.0).status == "rejected"
    assert ov.check(replace(BASE, amount=-3.0), 100.0).status == "booked"


def test_maker_checker_on_different_days() -> None:
    assert ov.check(replace(BASE, checker_date=""), 100.0).status == "proposed"
    d = ov.check(replace(BASE, checker_date=BASE.maker_date), 100.0)
    assert d.status == "proposed" and "later day" in d.problems[0]


def test_persistence_and_expiry() -> None:
    assert ov.check(replace(BASE, renewals=2), 100.0).status == "booked"
    assert ov.check(replace(BASE, renewals=3), 100.0).status == "rejected"
    assert (
        ov.check(replace(BASE, renewals=3, limitation_id="L-099", fix_date="2027-03-31"), 100.0).status
        == "booked"
    )
    assert ov.check(replace(BASE, expires="2026-09-30"), 100.0).status == "rejected"


def test_register_total_cap_books_nothing_without_an_exception() -> None:
    items = [replace(BASE, overlay_id=f"OV-{i}") for i in range(3)]  # 3 x 4% = 12% > 10%
    r = ov.register(items, 100.0, "202606")
    assert r["booked_total"] == 0.0 and {e["status"] for e in r["entries"]} == {"proposed"}
    items[0] = replace(items[0], cap_exception="written")
    r = ov.register(items, 100.0, "202606")
    assert r["booked_total"] == pytest.approx(12.0) and r["total_cap_flag"]
    assert ov.register(items, 100.0, "202603")["entries"] == []  # other dates are left out


def test_load_reads_the_log(tmp_path: Path) -> None:
    p = tmp_path / "log.json"
    p.write_text(json.dumps({"overlays": [BASE.__dict__], "assessments": [{"id": "A-1"}]}), encoding="utf-8")
    items, assessed = ov.load(p)
    assert items == [BASE] and assessed == [{"id": "A-1"}]
