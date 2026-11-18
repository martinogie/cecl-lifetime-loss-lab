"""The model's inputs (Phase 2 step 2): the split, the outcome, the features and their rules.

Pure definitions and small functions, so the rules are tested here and the same definitions drive
the DuckDB feature build on the PC (`ingestion/feature_build.py`), the models and, later, Spark.

Timing: one row per loan-month *t*. Every feature uses only what is known at the start of month
*t*: values from month *t − 1* (the loan's previous panel row) or from origination. The outcome is
what happens in month *t*. A model never sees the month it predicts.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------- split (ADR-009 A)
# By observation month (YYYYMM). Martins's decision, 16 November 2026.
TRAIN, VALIDATE, COVID, TEST = "train", "validate", "covid", "test"
SPLITS: tuple[tuple[str, str, str], ...] = (
    (TRAIN, "199901", "201612"),
    (VALIDATE, "201701", "201912"),
    (COVID, "202001", "202206"),  # left out of fitting and choosing; the sensitivity run uses it
    (TEST, "202207", "202606"),  # scored once, by the test command only (ADR-009 E)
)
HOLDOUT = (TEST,)  # written to a separate folder that training never reads


def split_of(period_ym: str) -> str:
    for name, first, last in SPLITS:
        if first <= period_ym <= last:
            return name
    raise ValueError(f"{period_ym!r} is outside every split (1999-01 to 2026-06)")


def split_sql(column: str = "period_ym") -> str:
    """The same rule as a SQL CASE expression (DuckDB and Spark)."""
    cases = " ".join(f"when {column} between '{a}' and '{b}' then '{n}'" for n, a, b in SPLITS)
    return f"case {cases} end"


# ---------------------------------------------------------------------------- outcome
# What happens to the loan in month t (ADR-002 statuses from the panel).
NONE, DEFAULT, PREPAY = 0, 1, 2
OUTCOME = {"current": NONE, "delinquent": NONE, "defaulted": DEFAULT, "prepaid": PREPAY}
# "censored" is not an outcome: the loan left the data that month (data end, a transfer, or a long
# gap) and whether it survived the month is unknown. Counting it as "no event" would understate
# defaults and prepayments, so censored months are left out (and counted in the build summary).
EXCLUDED_STATUS = "censored"


def outcome_of(status: str) -> int:
    if status not in OUTCOME:
        raise ValueError(f"no outcome for status {status!r}")
    return OUTCOME[status]


# ---------------------------------------------------------------------------- multi-state (ADR-011)
# Each live loan-month starts in one of three states and ends in one of five (ADR-002 unchanged):
# current, 30 and 60 days late (from the stop-the-clock count), default (90+ or a loss event, from
# any state; absorbing) and prepaid (absorbing). Censored months stay out (outcome unknown).
CURRENT, LATE_30, LATE_60, DEFAULTED, PREPAID = 0, 1, 2, 3, 4
STATES = ("current", "30", "60", "default", "prepaid")  # index = code
LIVE_STATES = (CURRENT, LATE_30, LATE_60)  # the starting states, one transition model each
# The adverse move of each step: its reason codes explain this move (ADR-009 Amendment 1, C).
ADVERSE = {CURRENT: LATE_30, LATE_30: LATE_60, LATE_60: DEFAULTED}


def state_of(status: str, months_late: int) -> int:
    """The state of a non-censored panel row (ADR-002): its status, then its stop-the-clock count."""
    if status == "defaulted":
        return DEFAULTED
    if status == "prepaid":
        return PREPAID
    if status not in ("current", "delinquent"):
        raise ValueError(f"no state for status {status!r}")
    if not 0 <= months_late <= 2:  # 3+ months late is a default (ADR-002 B1), never a live row
        raise ValueError(f"a live row cannot be {months_late} months late")
    return int(months_late)


def state_sql(status: str = "status", months_late: str = "effective_months_late") -> str:
    """The same rule as SQL (DuckDB and Spark)."""
    return (
        f"case when {status} = 'defaulted' then {DEFAULTED} when {status} = 'prepaid' then {PREPAID} "
        f"else {months_late} end"
    )


# ---------------------------------------------------------------------------- sampling
SAMPLE_EVERY = 25  # keep about 1 in 25 months with no event; every event is kept
SAMPLE_SEED = "cecl-phase2-v1"  # part of the hash: change it to draw a different sample
# The hash itself (identical in DuckDB and Spark) is in ingestion/feature_build.py (sample_condition).


# Weights: in each stratum (vintage x observation year), every kept no-event month stands for
# (no-event months in the stratum) / (kept ones), about 25; events have weight 1. So the weighted
# count of no-event months equals the panel's exactly, in every stratum (a tie-out), and every
# measure computed with the weights describes the full panel (ADR-009 consequences).


# ---------------------------------------------------------------------------- features
@dataclass(frozen=True)
class Feature:
    name: str
    meaning: str
    default_direction: int  # +1: more of it, more default (monotonic); -1: less; 0: not constrained
    prepay_direction: int


FEATURES: tuple[Feature, ...] = (
    Feature("loan_age", "months since the first payment", 0, 0),
    Feature("credit_score", "credit score at origination", -1, 0),
    Feature("current_ltv", "balance last month / (original value x state house-price change since origination), %", 1, 0),
    Feature("orig_dti", "debt-to-income at origination, %", 1, 0),
    Feature("rate_incentive", "loan's rate last month minus the market 30-year rate last month, points", 0, 1),
    Feature("months_late_prev", "months late last month (ADR-002 stop-the-clock count)", 1, 0),
    Feature("late_months_12", "months 30+ days late in the 12 months before t", 1, 0),
    Feature("unemployment", "state unemployment rate last month, %", 1, 0),
    Feature("unemployment_change_12", "change in the state unemployment rate over the 12 months to last month, points", 1, 0),
    Feature("forbearance_prev", "1 if last month was a flagged payment pause (ADR-002 D2; flags from 2014, L-016)", 0, 0),
    Feature("mi_pct", "mortgage insurance coverage at origination, %", 0, 0),
    Feature("orig_upb", "original balance, dollars", 0, 0),
    Feature("term_over_360", "1 if the original term is over 360 months (L-014)", 0, 0),
    Feature("purpose_cashout", "1 if a cash-out refinance", 0, 0),
    Feature("purpose_refi", "1 if a no-cash-out refinance", 0, 0),
    Feature("occupancy_investor", "1 if an investment property", 0, 0),
    Feature("occupancy_second", "1 if a second home", 0, 0),
    Feature("relief_refinance", "1 if a HARP / relief refinance (its DTI is always missing, L-024)", 0, 0),
    Feature("calendar_month", "month of the year of t (prepayment seasonality)", 0, 0),
)  # fmt: skip
FEATURE_NAMES = tuple(f.name for f in FEATURES)  # the feature table's columns
# The transition models' inputs: "months late last month" is the starting state itself, so it is
# constant within each transition model and left out (18 features).
TRANSITION_FEATURES = tuple(n for n in FEATURE_NAMES if n != "months_late_prev")

# Origination-era features (ADR-010, Option E): derived at model time from the loan's origination year
# (`vintage_year`), so the feature table needs no rebuild. "binary": originated 2009 or later (post-crisis
# underwriting); "buckets": 2005-2008 (bubble years) and 2009 on, with before-2005 as the reference.
# No monotonic constraint: the era's effect is for the data to show.
ERA_FEATURES: dict[str, tuple[Feature, ...]] = {
    "none": (),
    "binary": (
        Feature("originated_2009_on", "1 if originated in 2009 or later (post-crisis underwriting)", 0, 0),
    ),
    "buckets": (
        Feature("era_2005_2008", "1 if originated 2005-2008 (bubble years)", 0, 0),
        Feature("originated_2009_on", "1 if originated in 2009 or later (post-crisis underwriting)", 0, 0),
    ),
}
DERIVED = {
    "originated_2009_on": lambda vintage: (vintage >= 2009).astype(float),
    "era_2005_2008": lambda vintage: ((vintage >= 2005) & (vintage <= 2008)).astype(float),
}


def model_features(era: str = "none") -> tuple[Feature, ...]:
    """The features a model uses: the feature table's plus, optionally, the origination era."""
    if era not in ERA_FEATURES:
        raise ValueError(f"era must be one of {', '.join(ERA_FEATURES)}, got {era!r}")
    return FEATURES + ERA_FEATURES[era]


def current_ltv(
    upb_prev: float | None,
    orig_upb: float | None,
    orig_ltv: float | None,
    hpi_prev: float | None,
    hpi_orig: float | None,
) -> float | None:
    """Current LTV in %: last month's balance over the house's estimated value now.

    Value at origination = original balance / original LTV; value now = that x (state house-price
    index last month / index at origination). None if anything needed is missing.
    """
    if None in (upb_prev, orig_upb, orig_ltv, hpi_prev, hpi_orig):
        return None
    assert upb_prev is not None and orig_upb and orig_ltv and hpi_prev and hpi_orig
    value_now = orig_upb / (orig_ltv / 100.0) * (hpi_prev / hpi_orig)
    return 100.0 * upb_prev / value_now


def rate_incentive(rate_prev: float | None, market_prev: float | None) -> float | None:
    """Points by which the loan's rate exceeds the market rate: the refinance motive."""
    if rate_prev is None or market_prev is None:
        return None
    return rate_prev - market_prev


# ---------------------------------------------------------------------------- contract
# Sanity ranges for the feature table (a row outside stops the build). None means "no bound".
RANGES: dict[str, tuple[float | None, float | None]] = {
    "loan_age": (0, 600),
    "credit_score": (300, 850),
    # HARP 2.0 relief refinances had no LTV cap: 7 real rows sit between 446% and 665% (17 Nov 2026)
    "current_ltv": (0, 1000),
    "orig_dti": (0, 100),
    "rate_incentive": (-15, 15),
    "months_late_prev": (0, 400),
    "late_months_12": (0, 12),
    "unemployment": (0, 40),
    "unemployment_change_12": (-30, 30),
    "mi_pct": (0, 100),
    "orig_upb": (1, 10_000_000),
    "calendar_month": (1, 12),
}
# Largest share of missing values allowed per feature (above it, the build stops). Credit score,
# DTI and LTV can be "not available" in Freddie's data; state features are missing for Puerto Rico,
# Guam and the Virgin Islands (no FRED/FHFA state series, L-023).
MAX_MISSING_SHARE = 0.05
# Per-feature exceptions, each with its reason (and a limitations-log entry):
# debt-to-income is "not available" for 29-35% of the 2010-2013 vintages (11% in 2009, 15% in 2014,
# 0-4% otherwise; 6.95% over all vintages), consistent with Freddie's relief refinances (HARP years),
# where DTI is often not reported (L-024). The blank is kept as information: the logistic model gives
# it its own bin, LightGBM and the EBM treat missing as its own case.
MISSING_SHARE_EXCEPTIONS = {"orig_dti": 0.10}


def max_missing_share(feature: str) -> float:
    return MISSING_SHARE_EXCEPTIONS.get(feature, MAX_MISSING_SHARE)
