"""Plain-English reason codes for each loan's PD (Phase 2 step 8).

For each loan-month, the features that push its default score furthest **above** the average
(the family's per-feature contributions, cecl/models/families.py), up to three, written as a short
sentence with the loan's own value. The wording table is reviewed by Martins before use.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from cecl import features as fx


def _pct(v: float) -> str:
    return f"{v:.0f}%"


# feature -> sentence builder from the loan's value
WORDING: dict[str, Callable[[float], str]] = {
    "loan_age": lambda v: f"Loan age {v:.0f} months (the riskiest years of a mortgage)",
    "credit_score": lambda v: f"Credit score {v:.0f} at origination",
    "current_ltv": lambda v: (
        f"Current loan-to-value {_pct(v)}" + (" (owes more than the house is worth)" if v > 100 else "")
    ),
    "orig_dti": lambda v: f"Debt-to-income {_pct(v)} at origination",
    "rate_incentive": lambda v: (
        f"Loan rate {abs(v):.2f} points {'above' if v >= 0 else 'below'} today's market rate"
    ),
    "months_late_prev": lambda v: f"{v:.0f} month(s) behind on payments last month",
    "late_months_12": lambda v: f"{v:.0f} late payment(s) in the last 12 months",
    "unemployment": lambda v: f"State unemployment {v:.1f}%",
    "unemployment_change_12": lambda v: (
        f"State unemployment {'up' if v >= 0 else 'down'} {abs(v):.1f} points in a year"
    ),
    "forbearance_prev": lambda v: (
        "In a payment pause (forbearance) last month" if v else "Not in forbearance"
    ),
    "mi_pct": lambda v: f"Mortgage insurance covering {_pct(v)}",
    "orig_upb": lambda v: f"Original balance ${v:,.0f}",
    "term_over_360": lambda v: "Term longer than 30 years" if v else "Term of 30 years or less",
    "purpose_cashout": lambda v: "Cash-out refinance" if v else "Not a cash-out refinance",
    "purpose_refi": lambda v: "Refinance (no cash out)" if v else "Not a refinance",
    "occupancy_investor": lambda v: "Investment property" if v else "Not an investment property",
    "occupancy_second": lambda v: "Second home" if v else "Not a second home",
    "relief_refinance": lambda v: "HARP relief refinance" if v else "Not a relief refinance",
    "originated_2009_on": lambda v: (
        "Originated 2009 or later (post-crisis underwriting)" if v else "Originated before 2009"
    ),
    "era_2005_2008": lambda v: "Originated 2005-2008 (bubble years)" if v else "Not originated in 2005-2008",
    "calendar_month": lambda v: f"Month of the year: {v:.0f}",
    "state_last_month": lambda v: (
        "Up to date the month before" if v < 0.5 else f"{30 * int(round(v))} days late the month before"
    ),
    "hpi_gain": lambda v: (
        f"Home prices {'up' if v >= 0 else 'down'} {abs(100 * v):.0f}% since the loan was made"
    ),
    "sato": lambda v: (
        f"Loan rate {abs(v):.2f} points {'above' if v >= 0 else 'below'} the market rate when it was made"
    ),
    # model v2 (ADR-009 Amendment 3)
    "worst_state_6": lambda v: (
        "Up to date in the 6 months before"
        if v < 0.5
        else f"{30 * int(round(v))} days late in the past 6 months"
    ),
    "months_since_first_miss": lambda v: (
        "Never missed a payment" if v < 0.5 else f"First missed a payment {v:.0f} months ago"
    ),
    "ever_missed": lambda v: "Has missed a payment before" if v else "Has never missed a payment",
    "paused_12": lambda v: (
        "Payment pause in the last 12 months" if v else "No payment pause in the last 12 months"
    ),
    "locked_1": lambda v: "Loan rate 1+ point below market (rate lock)" if v else "Not rate-locked (1 point)",
    "locked_2": lambda v: (
        "Loan rate 2+ points below market (deep rate lock)" if v else "Not deeply rate-locked"
    ),
    "locked_x_age": lambda v: (
        f"Rate-locked loan, {v:.0f} years old" if v > 0 else "Not a rate-locked seasoned loan"
    ),
    "cpi_inflation_12": lambda v: f"Consumer prices up {v:.1f}% in a year",
    "real_income_growth_12": lambda v: (
        f"Real household income {'up' if v >= 0 else 'down'} {abs(v):.1f}% in a year"
    ),
    "card_delinquency": lambda v: f"Credit-card delinquency at {v:.1f}% nationally",
}


def sentence(feature: str, value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return f"{feature.replace('_', ' ').capitalize()} not available"
    return WORDING[feature](float(value))


def top_reasons(contributions: np.ndarray, values: dict[str, np.ndarray], k: int = 3,
                features: Sequence[str] = fx.FEATURE_NAMES) -> list[list[str]]:  # fmt: skip
    """Up to k reasons per row: the features with the largest positive contributions to default."""
    c = np.asarray(contributions, dtype=float)
    out = []
    for i in range(c.shape[0]):
        order = [j for j in np.argsort(-c[i]) if c[i, j] > 0][:k]
        out.append([sentence(features[j], values[features[j]][i]) for j in order])
    return out


def wording_table() -> list[dict[str, str]]:
    """The wording reviewed by Martins: one example sentence per feature."""
    examples = {"loan_age": 40, "credit_score": 640, "current_ltv": 112, "orig_dti": 45, "rate_incentive": 1.5,
                "months_late_prev": 2, "late_months_12": 3, "unemployment": 7.8, "unemployment_change_12": 3.1,
                "forbearance_prev": 1, "mi_pct": 25, "orig_upb": 250000, "term_over_360": 1, "purpose_cashout": 1,
                "purpose_refi": 1, "occupancy_investor": 1, "occupancy_second": 1, "relief_refinance": 1, "originated_2009_on": 1, "era_2005_2008": 1, "calendar_month": 7,
                "state_last_month": 2, "hpi_gain": 0.35, "sato": 0.6, "worst_state_6": 1,
                "months_since_first_miss": 14, "ever_missed": 1, "paused_12": 1, "locked_1": 1, "locked_2": 1,
                "locked_x_age": 6, "cpi_inflation_12": 6.5, "real_income_growth_12": -2.0, "card_delinquency": 3.4}  # fmt: skip
    names = [f.name for f in fx.model_features("buckets") + fx.V2_ALL_EXTRA]
    return [{"feature": f, "example": sentence(f, examples[f])} for f in names]
