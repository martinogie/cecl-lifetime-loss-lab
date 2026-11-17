"""ADR-009's selection rule, as code (Martins's Decision, 16 November 2026).

C1: the logistic benchmark stays unless a challenger
  - improves validation log loss (default) by at least 2%,
  - passes every calibration bar (default and prepayment; 0.80-1.25 for every year and vintage with
    at least 200 defaults; cecl/metrics.py), and
  - produces reason codes.
Ties go to the simpler model: among challengers that qualify, the one with the lowest log loss wins,
unless a simpler qualifying family is within 2% of it. D: one family covers both causes.
Pure logic on summary numbers, so the rule is tested and the validation report shows exactly why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cecl.models.families import SIMPLICITY_ORDER

BENCHMARK = "logistic"
MIN_IMPROVEMENT = 0.02  # ADR-009 C1


@dataclass(frozen=True)
class FamilyResult:
    name: str
    log_loss_default: float
    calibration_passes: bool  # every applicable bar, both causes, years and vintages
    reason_codes: bool


@dataclass
class Choice:
    chosen: str
    reasons: list[str] = field(default_factory=list)
    table: list[dict[str, Any]] = field(default_factory=list)


def improvement(benchmark: float, challenger: float) -> float:
    """Relative log-loss improvement of a challenger over the benchmark (0.02 = 2% lower)."""
    return (benchmark - challenger) / benchmark


def choose(results: list[FamilyResult]) -> Choice:
    by_name = {r.name: r for r in results}
    if BENCHMARK not in by_name:
        raise ValueError("the logistic benchmark's result is required")
    bench = by_name[BENCHMARK]
    choice = Choice(BENCHMARK)
    qualifying = []
    for r in sorted(results, key=lambda r: SIMPLICITY_ORDER.index(r.name)):
        gain = improvement(bench.log_loss_default, r.log_loss_default)
        row = {"family": r.name, "log_loss_default": r.log_loss_default, "improvement": gain,
               "calibration_passes": r.calibration_passes, "reason_codes": r.reason_codes}  # fmt: skip
        if r.name != BENCHMARK:
            missing = []
            if gain < MIN_IMPROVEMENT:
                missing.append(f"log loss {gain:.1%} better than the benchmark (needs {MIN_IMPROVEMENT:.0%})")
            if not r.calibration_passes:
                missing.append("fails a calibration bar")
            if not r.reason_codes:
                missing.append("no reason codes")
            row["qualifies"] = not missing
            if missing:
                choice.reasons.append(f"{r.name} does not qualify: " + "; ".join(missing))
            else:
                qualifying.append(r)
        choice.table.append(row)
    if not bench.calibration_passes:
        choice.reasons.append("note: the logistic benchmark itself fails a calibration bar (recorded; it still stays "
                              "unless a challenger qualifies)")  # fmt: skip
    if qualifying:
        best = min(qualifying, key=lambda r: r.log_loss_default)
        close = [
            r for r in qualifying if improvement(r.log_loss_default, best.log_loss_default) < MIN_IMPROVEMENT
        ]
        winner = min(close, key=lambda r: SIMPLICITY_ORDER.index(r.name))
        choice.chosen = winner.name
        choice.reasons.append(
            f"{winner.name} chosen: it qualifies"
            + (
                f", and is the simplest family within {MIN_IMPROVEMENT:.0%} of the best ({best.name})"
                if winner is not best
                else ", with the lowest log loss of those that do"
            )  # fmt: skip
        )
    else:
        choice.reasons.append("no challenger qualifies: the logistic benchmark stays")
    return choice
