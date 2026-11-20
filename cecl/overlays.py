"""Overlay framework (ADR-006): qualitative adjustments on top of the modelled reserve, under fixed rules.

An overlay is a documented human change for something the model can't see. ADR-006 (Martins, 14 November
2026) allows them only if each one:
- names one of the standard qualitative factors (2020 interagency policy statement), Q4 and Q6 not applicable;
- is sized by the numbers method (re-run the model with one input stressed, take the difference); a plain
  percentage needs extra written justification;
- stays within 5% of the modelled reserve per overlay and 10% in total, or carries a written cap exception
  that the memo flags;
- if it lowers the reserve, uses the numbers method and has the checker's written sign-off;
- expires at the quarter-end of its reserve date unless renewed with fresh evidence;
- if renewed for more than two quarters, becomes a model fix (a limitations-log item with a fix date);
- is approved maker/checker, the checker review on a different day from the maker's.

Only overlays that pass every rule and are approved are **booked**. Everything else stays in the register as
"proposed" or "rejected" with the reasons, so a reviewer sees what was considered. The modelled reserve is
always reported first; overlays are added on top and never mixed into model outputs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

FACTORS = {
    "Q1": ("Lending policies and procedures", True),
    "Q2": ("Economic conditions", True),
    "Q3": ("Nature and volume of the portfolio and terms of loans", True),
    "Q4": ("Experience, ability and depth of lending staff", False),  # no real bank staff
    "Q5": ("Volume and severity of past-due, nonaccrual and classified loans", True),
    "Q6": ("Quality of the credit review function", False),  # no real credit review function
    "Q7": ("Value of underlying collateral", True),
    "Q8": ("Concentrations of credit", True),
    "Q9": ("Other external factors (competition, legal, regulatory)", True),
}
CAP_EACH = 0.05  # ADR-006: share of the modelled reserve, per overlay
CAP_TOTAL = 0.10  # ADR-006: share of the modelled reserve, all overlays together
MAX_RENEWALS = 2  # renewed for more than two quarters -> the model must be fixed
METHODS = ("numbers", "percentage")


def quarter_end(reserve_ym: str) -> date:
    """The last day of the quarter holding `reserve_ym` (YYYYMM)."""
    y, m = int(reserve_ym[:4]), int(reserve_ym[4:])
    qm = 3 * ((m - 1) // 3 + 1)  # the quarter's last month
    return date(y + qm // 12, qm % 12 + 1, 1) - timedelta(days=1)


def size_by_rerun(modelled: float, stressed: float) -> float:
    """The numbers method: the overlay is the stressed re-run's reserve minus the modelled reserve."""
    return float(stressed) - float(modelled)


@dataclass
class Overlay:
    """One overlay as proposed by the maker. `amount` is signed (a decrease is negative)."""

    overlay_id: str
    reserve_ym: str
    factor: str
    segment: str
    amount: float
    method: str
    evidence: str
    maker: str
    maker_date: str  # ISO date
    checker: str = ""
    checker_date: str = ""  # ISO date; empty until the checker signs
    justification: str = ""  # required for the percentage method
    cap_exception: str = ""  # written justification for going above a cap
    limitation_id: str = (
        ""  # limitations-log entry (needed for a model-limitation overlay or a persistent one)
    )
    fix_date: str = ""  # the model-fix date, needed once the overlay is persistent
    renewals: int = 0  # consecutive quarters this same overlay was booked before
    expires: str = ""  # ISO date; filled with the quarter-end if empty


@dataclass
class Decision:
    overlay: Overlay
    status: str  # booked / proposed (waiting for the checker) / rejected
    problems: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # booked but must be shown in the memo (cap exception)
    share_of_modelled: float = 0.0


def check(o: Overlay, modelled: float) -> Decision:
    """Apply the ADR-006 rules to one overlay against the modelled reserve for the same date."""
    problems, flags = [], []
    if o.factor not in FACTORS:
        problems.append(f"factor {o.factor!r} is not one of Q1-Q9")
    elif not FACTORS[o.factor][1]:
        problems.append(f"factor {o.factor} ({FACTORS[o.factor][0]}) is recorded as not applicable")
    if o.method not in METHODS:
        problems.append(f"method {o.method!r} must be one of {METHODS}")
    if o.method == "percentage" and not o.justification.strip():
        problems.append("a percentage overlay needs a written justification")
    if not o.evidence.strip():
        problems.append("no evidence recorded")
    share = o.amount / modelled if modelled else float("inf")
    if abs(share) > CAP_EACH:
        if o.cap_exception.strip():
            flags.append(
                f"above the {CAP_EACH:.0%} cap per overlay ({share:+.1%}): written exception, flag in memo"
            )
        else:
            problems.append(f"{share:+.1%} of the modelled reserve is above the {CAP_EACH:.0%} cap per overlay, "
                            "with no written exception")  # fmt: skip
    if o.amount < 0:
        if o.method != "numbers":
            problems.append("a decrease must use the numbers method")
        if not o.checker_date:
            problems.append("a decrease needs the checker's written sign-off")
    if o.renewals > MAX_RENEWALS and not (o.limitation_id and o.fix_date):
        problems.append(f"renewed {o.renewals} quarters (more than {MAX_RENEWALS}): the model must be fixed; "
                        "open a limitations-log item with a fix date")  # fmt: skip
    end = quarter_end(o.reserve_ym).isoformat()
    if o.expires and o.expires > end:
        problems.append(f"expires {o.expires}, after the quarter-end {end}; renew each quarter instead")
    if problems:
        status = "rejected"
    elif not o.checker_date:
        status = "proposed"
    elif o.checker_date <= o.maker_date:
        status, problems = "proposed", ["the checker review must be on a later day than the maker's"]
    else:
        status = "booked"
    return Decision(o, status, problems, flags, share)


def register(overlays: list[Overlay], modelled: float, reserve_ym: str) -> dict[str, Any]:
    """Every overlay for one reserve date, checked one by one and against the total cap.

    If the booked overlays together break the 10% total cap and none of them carries a cap exception, none
    is booked: the maker must cut or justify them, so the order of entries never decides what gets in."""
    decisions = [check(o, modelled) for o in overlays if o.reserve_ym == reserve_ym]
    booked = [d for d in decisions if d.status == "booked"]
    total = sum(d.overlay.amount for d in booked)
    total_share = total / modelled if modelled else 0.0
    total_flag = ""
    if abs(total_share) > CAP_TOTAL:
        if any(d.overlay.cap_exception.strip() for d in booked):
            total_flag = f"booked overlays {total_share:+.1%} of the modelled reserve, above the {CAP_TOTAL:.0%} total cap: written exception, flag in memo"
        else:
            for d in booked:
                d.status = "proposed"
                d.problems.append(
                    f"booked overlays together {total_share:+.1%}, above the {CAP_TOTAL:.0%} total cap"
                )
            booked, total, total_share = [], 0.0, 0.0
    return {
        "reserve_ym": reserve_ym,
        "quarter_end": quarter_end(reserve_ym).isoformat(),
        "modelled_reserve": modelled,
        "booked_total": total,
        "booked_share_of_modelled": total_share,
        "total_cap_flag": total_flag,
        "entries": [
            asdict(d.overlay)
            | {
                "expires": d.overlay.expires or quarter_end(reserve_ym).isoformat(),
                "status": d.status,
                "problems": d.problems,
                "flags": d.flags,
                "share_of_modelled": d.share_of_modelled,
                "factor_name": FACTORS.get(d.overlay.factor, ("unknown", False))[0],
            }
            for d in decisions
        ],
    }


def load(path: Path) -> tuple[list[Overlay], list[dict[str, Any]]]:
    """The overlay log: proposed overlays and the considered-but-not-proposed assessments (aggregates only)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Overlay(**o) for o in raw.get("overlays", [])], list(raw.get("assessments", []))
