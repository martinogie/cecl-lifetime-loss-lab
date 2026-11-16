"""Tie-outs (controls C02 and, from Phase 4, C04): two numbers that must agree, or the run stops.

A tie-out compares a number from one place (the source file, an earlier layer) with the same number
counted again somewhere else (a later layer). If nothing was lost or double counted, they agree
exactly. Money is compared as exact decimals, so "agree" means to the cent, not "close enough".

Some differences are expected and explained (for example, recent loans with no monthly rows yet,
L-015). Those are never hidden inside a tolerance: they are counted separately and the tie-out
includes them explicitly ("loans in origination = loans in the panel + loans with no monthly rows").

Pure logic, no files: the local pipeline, Airflow and (Phase 1 step 9) Databricks use the same
checks, and every result goes into the run log whether it passes or not.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

Number = int | Decimal


class TieOutError(RuntimeError):
    """One or more tie-outs failed; the run stops and nothing downstream uses the data."""

    def __init__(self, failed: list[TieOut]) -> None:
        self.failed = failed
        lines = [
            f"{t.name}: expected {t.expected}, got {t.actual} (difference {t.difference})" for t in failed
        ]
        super().__init__("tie-out failed:\n  " + "\n  ".join(lines))


@dataclass(frozen=True)
class TieOut:
    name: str  # what is compared, in plain words, e.g. "vintage 2007: loans, bronze vs silver"
    expected: Number  # from the source or the earlier layer
    actual: Number  # counted again in the later layer
    control: str = "C02"

    def __post_init__(self) -> None:
        # A float would make "to the cent" meaningless (0.1 + 0.2 != 0.3), so it is refused.
        for value in (self.expected, self.actual):
            if isinstance(value, float):
                raise TypeError(f"{self.name}: tie-outs take int or Decimal, not float")

    @property
    def difference(self) -> Number:
        return self.actual - self.expected

    @property
    def ok(self) -> bool:
        return self.difference == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "name": self.name,
            "expected": str(self.expected),
            "actual": str(self.actual),
            "ok": self.ok,
        }


def enforce(tie_outs: Iterable[TieOut]) -> list[TieOut]:
    """Return the tie-outs if all agree; otherwise raise TieOutError listing every failure."""
    checked = list(tie_outs)
    failed = [t for t in checked if not t.ok]
    if failed:
        raise TieOutError(failed)
    return checked
