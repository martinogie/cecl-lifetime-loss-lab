"""The model's inputs (Phase 2 step 2): the split, the outcome, the features and their rules.

Pure definitions and small functions, so the rules are tested here and the same definitions drive
the DuckDB feature build on the PC (`ingestion/feature_build.py`), the models and, later, Spark.

Timing: one row per loan-month *t*. Every feature uses only what is known at the start of month
*t*: values from month *t − 1* (the loan's previous panel row) or from origination. The outcome is
what happens in month *t*. A model never sees the month it predicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

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
# Inputs used by the transition models only (approved by Martins 18 November 2026, after the step-0
# diagnoses in limitations log L-030 and L-028). Directions are the monotone constraints' business
# reasons (cecl/models/transitions.py applies them by role: rolls to worse states follow the default
# direction, cures the opposite, payoffs the prepay direction):
# - state_last_month: a loan that was late last month, or is recovering from 60, rolls back far more
#   (current -> 30: 0.5% / 19.5% / 30.0% a month by last month's state in 1999-2016), and a recent late
#   payment blocks a refinance (payoffs 1.46% / 1.08% / 1.04%). Free in the 30- and 60-day models:
#   among loans already late the path is not one-directional (60 -> default: 28% / 45% / 27%).
# - hpi_gain: more equity, less default (60 -> default 48% at a loss of over 10% down to 29% at a gain
#   of over 50%); payoffs left free, because the effect is not one-directional in the data (underwater
#   relief refinances paid off fast in 2009-2013).
# - sato: a rate above the market rate at origination marks a riskier borrower (current -> 30: 0.36%
#   a month at 0.5 points below market, 1.03% at 0.5 above); payoffs left free (the effect overlaps
#   the rate incentive).
TRANSITION_EXTRA: tuple[Feature, ...] = (
    Feature("state_last_month", "the loan's state the month before its starting state: 0 current, 1 = 30 days, 2 = 60 days late", 1, -1),
    Feature("hpi_gain", "state house-price change from the origination month to last month (index ratio - 1)", -1, 0),
    Feature("sato", "loan's rate at origination minus the market 30-year rate in the origination month, points", 1, 0),
)  # fmt: skip
# The transition models' inputs: "months late last month" is the starting state itself, so it is
# constant within each transition model and left out (18 features), plus the three above (21).
TRANSITION_FEATURES = tuple(n for n in FEATURE_NAMES if n != "months_late_prev") + tuple(
    f.name for f in TRANSITION_EXTRA
)

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
    "state_last_month": (0, 2),
    "hpi_gain": (-1, 20),
    "sato": (-10, 10),
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


# ---------------------------------------------------------------------------- model v2 (ADR-009 Amendment 3)
# Approved by Martins 19 November 2026 (docs/phase2/model_v2_plan.md). v1's definitions above are
# unchanged, so v1's tables and models can be rebuilt exactly; v2 has its own split and tables.
V2_TRAIN, V2_COVID, V2_HOLDOUT = "train", "covid", "holdout"
V2_SPLITS: tuple[tuple[str, str, str], ...] = (
    (V2_TRAIN, "199901", "201912"),
    (V2_COVID, "202001", "202206"),  # not fitted (decision D4); used only to compute history inputs
    (V2_TRAIN, "202207", "202406"),
    (V2_HOLDOUT, "202407", "202606"),  # scored once, by the holdout command only
)
V2_HOLDOUT_SPLITS = (V2_HOLDOUT,)
# Walk-forward exams (last training month, first scored month, last scored month). F1-F4 tune and
# select; F5 is reported only (information). Each trains on every training month up to its cut.
V2_FOLDS: tuple[tuple[str, str, str], ...] = (
    ("200712", "200801", "201012"),  # F1: the crisis
    ("201012", "201101", "201312"),  # F2
    ("201312", "201401", "201612"),  # F3
    ("201612", "201701", "201912"),  # F4
)
V2_INFO_FOLD = ("201912", "202207", "202406")  # F5, information only
V2_FOLD_NAMES = ("F1 2008-2010", "F2 2011-2013", "F3 2014-2016", "F4 2017-2019")


def v2_split_of(period_ym: str) -> str:
    for name, first, last in V2_SPLITS:
        if first <= period_ym <= last:
            return name
    raise ValueError(f"{period_ym!r} is outside every v2 split (1999-01 to 2026-06)")


def v2_split_sql(column: str = "period_ym") -> str:
    cases = " ".join(f"when {column} between '{a}' and '{b}' then '{n}'" for n, a, b in V2_SPLITS)
    return f"case {cases} end"


# New stored inputs (built by ingestion/feature_build.py with v2=True). Directions as in v1: +1 means
# more of it, more rolls to worse states (monotone in LightGBM, bins in the logistic model); 0 free.
V2_EXTRA: tuple[Feature, ...] = (
    Feature("worst_state_6", "worst state in the 6 months before the starting month (t-7 to t-2): 0 current, 1 = 30, 2 = 60 days late", 1, -1),
    Feature("months_since_first_miss", "calendar months from the loan's first 30+ days late month (up to t-1) to t, capped at 60; 0 if never late", 0, 0),
    Feature("paused_12", "1 if any of the 12 months before t was a flagged payment pause (flags from 2014, L-016)", 1, -1),
)  # fmt: skip
# Household-stress candidates (national, 12-month change to t-1). Only those with a Fed scenario path
# are eligible (decision D3); card delinquency is screened for information only.
V2_STRESS: tuple[Feature, ...] = (
    Feature("cpi_inflation_12", "CPI inflation over the 12 months to t-1, % (FRED CPIAUCSL)", 1, 0),
    Feature("real_income_growth_12", "real disposable income growth over the 12 months to t-1, % (FRED DSPIC96)", -1, 0),
    Feature("card_delinquency", "bank credit-card delinquency rate, latest quarter ended by t-1, % (FRED DRCCLACBS)", 1, 0),
)  # fmt: skip
V2_STRESS_ELIGIBLE = ("cpi_inflation_12", "real_income_growth_12")
# Rate lock (finding 1 of the out-of-time diagnosis), derived at model time from the rate incentive and
# loan age, so the projection gets them for free. No monotone constraint (v1's lesson: the direction of
# "below market" is not known in advance). Cut points fixed now (1 and 2 points), not searched.
V2_DERIVED_FEATURES: tuple[Feature, ...] = (
    Feature("locked_1", "1 if the loan rate is at least 1 point below the market rate (rate lock)", 0, 0),
    Feature("locked_2", "1 if the loan rate is at least 2 points below the market rate", 0, 0),
    Feature("locked_x_age", "locked_1 x loan age in years (seasoned locked loans fell behind most)", 0, 0),
    Feature("ever_missed", "1 if the loan has ever been 30+ days late (so 'never' is not read as 0 months)", 1, -1),
)  # fmt: skip
V2_DERIVED = {
    "locked_1": lambda X: (np.asarray(X["rate_incentive"], dtype=float) <= -1.0).astype(float),
    "locked_2": lambda X: (np.asarray(X["rate_incentive"], dtype=float) <= -2.0).astype(float),
    "locked_x_age": lambda X: (
        (np.asarray(X["rate_incentive"], dtype=float) <= -1.0).astype(float)
        * np.asarray(X["loan_age"], dtype=float)
        / 12.0
    ),
    "ever_missed": lambda X: (np.asarray(X["months_since_first_miss"], dtype=float) > 0).astype(float),
}
V2_STORED = TRANSITION_FEATURES + tuple(f.name for f in V2_EXTRA)  # columns the v2 table must carry
V2_BASE_FEATURES = V2_STORED + tuple(f.name for f in V2_DERIVED_FEATURES)  # 28 inputs before stress
V2_ALL_EXTRA = TRANSITION_EXTRA + V2_EXTRA + V2_STRESS + V2_DERIVED_FEATURES
RANGES.update({"worst_state_6": (0, 2), "months_since_first_miss": (0, 60), "paused_12": (0, 1),
               "cpi_inflation_12": (-5, 20), "real_income_growth_12": (-40, 40), "card_delinquency": (0, 20)})  # fmt: skip


def v2_features(stress: tuple[str, ...] = ()) -> tuple[str, ...]:
    """v2's inputs: all v1 inputs, the memory and rate-lock inputs, plus the kept stress inputs."""
    bad = [s for s in stress if s not in {f.name for f in V2_STRESS}]
    if bad:
        raise ValueError(f"unknown household-stress input(s): {bad}")
    return V2_BASE_FEATURES + tuple(stress)


def with_derived(X: dict[str, Any], features: tuple[str, ...]) -> dict[str, Any]:
    """Adds any derived v2 input the feature list asks for (computed from the stored ones)."""
    out = dict(X)
    for name in features:
        if name in V2_DERIVED and name not in out:
            out[name] = V2_DERIVED[name](out)
    return out
