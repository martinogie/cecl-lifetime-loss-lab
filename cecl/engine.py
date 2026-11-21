"""The CECL engine: lifetime expected loss per loan and for the book (Phase 4; roadmap 5.4).

    expected loss = sum over future months k of  P(default in month k) x EAD_k x LGD_k

- **P(default in month k)**, k = 1 to 36: the v2 path simulation (`cecl.models.simulate`, `first_state` =
  each loan's actual state at the reserve date), fed the scenario's economy (`cecl.scenarios`): 24 months
  of forecast, then 12 months of reversion.
- **After month 36, to maturity, in yearly steps** (Martins, 19 November 2026): the economy is at its
  long-run values, so each loan's monthly chances of default and payoff are held at their month-36 level
  (conditional on the loan being alive), and applied year by year until the loan matures. A simplification:
  loan-age effects after year 3 are not re-scored; the tail's share of the reserve is reported.
- **EAD_k**: the payment schedule less partial prepayment (`cecl.ead`, Phase 3).
- **LGD_k**: the Phase 3 two-step model fed the loan's LTV that month (EAD over the original value moved by
  the scenario's state house-price path) and the path's 12-month house-price change.

Tie-out: the book reserve equals the sum of the loan reserves (`controls`, checked by the caller).
Educational model on Freddie Mac conforming mortgages; not an audited reserve and not accounting advice.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cecl import ead, lgd
from cecl.models import multistate

MONTHLY = 36  # months projected month by month (forecast 24 + reversion 12)
MAX_YEARS = 31  # yearly tail steps (a 30-year loan never needs more)


def lgd_features(book: Any, hpi_now: np.ndarray, hpi_12: np.ndarray, balance: np.ndarray) -> np.ndarray:
    """The Phase 3 LGD inputs for each loan if it defaulted with `balance` owed, given its state's house
    price index now and 12 months earlier."""
    value = book["orig_upb"].to_numpy(dtype=float) / (book["orig_ltv"].to_numpy(dtype=float) / 100.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ltv = 100.0 * balance / (value * hpi_now / book["hpi_orig"].to_numpy(dtype=float))
        change = hpi_now / hpi_12 - 1.0
    f = {"ltv_default": ltv, "hpi_change_12": change, "mi_pct": book["mi_pct"].to_numpy(dtype=float),
         "judicial": book["property_state"].isin(lgd.JUDICIAL).to_numpy(dtype=float),
         "log_orig_upb": np.log(book["orig_upb"].to_numpy(dtype=float)),
         "credit_score": book["credit_score"].to_numpy(dtype=float),
         "investor": book["occupancy_investor"].to_numpy(dtype=float)}  # fmt: skip
    return np.nan_to_num(np.column_stack([f[c] for c in lgd.FEATURES]), nan=0.0)


def tail_hazards(p_def: np.ndarray, p_pre: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """From the monthly projection (columns = months 1..K): each loan's survival after month K and its
    month-K chances of default and payoff given it is alive (the long-run hazards for the tail)."""
    exits = (p_def + p_pre).cumsum(axis=1)
    alive_before_last = np.clip(1.0 - (exits[:, -2] if p_def.shape[1] > 1 else 0.0), 0.0, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        h_d = np.where(alive_before_last > 1e-12, p_def[:, -1] / alive_before_last, 0.0)
        h_p = np.where(alive_before_last > 1e-12, p_pre[:, -1] / alive_before_last, 0.0)
    survival = np.clip(1.0 - exits[:, -1], 0.0, 1.0)
    return survival, np.clip(h_d, 0.0, 1.0), np.clip(h_p, 0.0, 1.0)


def quarters(el: np.ndarray, p_def: np.ndarray, outstanding: np.ndarray) -> dict[str, np.ndarray]:
    """Monthly loan x month arrays summed into loan x quarter arrays (months 1-3 = quarter 1, ...)."""
    n, K = el.shape
    q = K // 3

    def by_q(a: np.ndarray) -> np.ndarray:
        return a[:, : 3 * q].reshape(n, q, 3).sum(axis=2)

    return {"q_el": by_q(el), "q_pd": by_q(p_def), "q_bal": by_q(outstanding)}


def price(book: Any, p_def: np.ndarray, p_pre: np.ndarray, macro: multistate.Macro, reserve_ym: str,
          lgd_model: Any, curtail: dict[int, float], long_run_growth: dict[str, float],
          forecast_months: int = 24, quarterly: bool = False, monthly: bool = False) -> dict[str, np.ndarray]:  # fmt: skip
    """Each loan's lifetime expected loss under one scenario. `p_def`, `p_pre`: loans x MONTHLY (months 1..36
    after the reserve month). `long_run_growth`: each state's annual log house-price growth (and "US").
    `forecast_months` only splits the reported loss into forecast and the rest (ADR-003 default 24); the
    economy itself, including the reversion, is already in `macro`.

    `quarterly` (Phase 5): also return, for each of the K / 3 quarters after the reserve date, each loan's
    expected loss (`q_el`), expected defaults (`q_pd`) and expected balance still outstanding at the end of
    each month, summed over the quarter's months (`q_bal`; divide by 3 for the quarter's average).
    `monthly` (Phase 6): also return each loan's expected loss for each of the K months (`el_monthly`), so a
    backtest can count only the months whose outcome is known."""
    n, K = p_def.shape
    outstanding = np.zeros((n, K))
    alive = 1.0 - np.cumsum(p_def + p_pre, axis=1)  # chance the loan is still open at the end of month k
    states = book["property_state"].astype(str).to_numpy()
    args = (book["upb_start"], book["rate_start"], book["remaining_start"], book["loan_age"])
    el = np.zeros((n, K))
    ead_sum = np.zeros(n)
    for k in range(1, K + 1):
        ym = multistate.add_months(reserve_ym, k)
        bal = ead.project(*args, k, curtail)  # k payments after the start of the reserve month
        hpi_now = macro.by_state(macro.hpi, ym, states)
        hpi_12 = macro.by_state(macro.hpi, multistate.add_months(ym, -12), states)
        el[:, k - 1] = p_def[:, k - 1] * bal * lgd_model.predict(lgd_features(book, hpi_now, hpi_12, bal))
        ead_sum += p_def[:, k - 1] * bal
        outstanding[:, k - 1] = np.clip(alive[:, k - 1], 0.0, 1.0) * bal
    # the tail: yearly steps at the month-36 hazards and the long-run house-price growth
    survival, h_d, h_p = tail_hazards(p_def, p_pre)
    h = h_d + h_p
    with np.errstate(divide="ignore", invalid="ignore"):
        share_d = np.where(h > 0, h_d / h, 0.0)
    g = np.array([long_run_growth.get(s, long_run_growth["US"]) for s in states])
    hpi_K = macro.by_state(macro.hpi, multistate.add_months(reserve_ym, K), states)
    remaining_after = book["remaining_start"].to_numpy(dtype=float) - K
    tail_el, tail_pd = np.zeros(n), np.zeros(n)
    s_start = survival.copy()
    for y in range(1, MAX_YEARS + 1):
        live = remaining_after > 12 * (y - 1)
        if not live.any():
            break
        p_year_exit = 1.0 - (1.0 - h) ** 12
        d_year = np.where(live, s_start * p_year_exit * share_d, 0.0)
        mid = K + 12 * y - 6
        bal = ead.project(*args, mid, curtail)
        hpi_mid = hpi_K * np.exp(g * (12 * y - 6) / 12.0)
        lgd_mid = lgd_model.predict(lgd_features(book, hpi_mid, hpi_mid / np.exp(g), bal))
        tail_el += d_year * bal * lgd_mid
        tail_pd += d_year
        ead_sum += d_year * bal
        s_start = np.where(live, s_start * (1.0 - p_year_exit), s_start)
    return {"forecast": el[:, :forecast_months].sum(axis=1), "reversion": el[:, forecast_months:].sum(axis=1), "tail": tail_el,
            "total": el.sum(axis=1) + tail_el, "lifetime_pd": p_def.sum(axis=1) + tail_pd,
            "pd_36": p_def.sum(axis=1), "ead_at_default": ead_sum} | (quarters(el, p_def, outstanding) if quarterly else {}) | ({"el_monthly": el} if monthly else {})  # fmt: skip
