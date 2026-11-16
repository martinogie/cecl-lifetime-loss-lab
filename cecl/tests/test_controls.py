from __future__ import annotations

from decimal import Decimal

import pytest

from cecl import controls


def test_tie_out_agrees_to_the_cent() -> None:
    t = controls.TieOut("balance", Decimal("100.10"), Decimal("100.1000"))
    assert t.ok and t.difference == 0
    assert t.as_dict() == {
        "control": "C02",
        "name": "balance",
        "expected": "100.10",
        "actual": "100.1000",
        "ok": True,
    }
    assert not controls.TieOut("balance", Decimal("100.10"), Decimal("100.11")).ok


def test_floats_are_refused() -> None:
    with pytest.raises(TypeError, match="not float"):
        controls.TieOut("x", 0.1, Decimal("0.1"))  # type: ignore[arg-type]


def test_enforce_lists_every_failure() -> None:
    good = controls.TieOut("loans", 10, 10)
    assert controls.enforce([good]) == [good]
    bad1, bad2 = controls.TieOut("loans", 10, 9), controls.TieOut("rows", 5, 7, control="C04")
    with pytest.raises(controls.TieOutError) as info:
        controls.enforce([good, bad1, bad2])
    assert info.value.failed == [bad1, bad2]
    assert "loans: expected 10, got 9 (difference -1)" in str(info.value)
    assert "rows: expected 5, got 7 (difference 2)" in str(info.value)
