"""Phase 6 backtests: the pieces shared by the Databricks job and the PC (rules: docs/phase6/test_designs.md).

- `flat_macro`: economy (b) of the rolling backtest, "flat then reverted", built only from data up to the
  reserve date (point in time).
- `long_run_growth`: each state's long-run annual house-price growth up to a date (for the tail).
- `counted_to_censoring`: projected chances summed only over months whose outcome is known (L-039).
- `crisis_verdict`, `rolling_verdict`, `forbearance_verdict`: the pass rules exactly as committed before
  the tests ran.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from cecl.models import multistate

BAR = (0.80, 1.25)  # the holdout bar
BAND = (0.67, 1.50)  # single years or single dates
LONG_RUN_START = "199001"  # ADR-003: long-run averages from 1990


def _months_after(ym: str, n: int) -> list[str]:
    return [multistate.add_months(ym, k) for k in range(1, n + 1)]


def long_run_growth(macro: multistate.Macro, as_of: str, start: str = LONG_RUN_START) -> dict[str, float]:
    """Mean annual log house-price growth per state from `start` to `as_of` (data known at `as_of`), plus
    "US" (the mean over states)."""
    h = macro.hpi[(macro.hpi.index >= start) & (macro.hpi.index <= as_of)]
    years = (len(h) - 1) / 12.0
    out = {str(s): float(np.log(h[s].iloc[-1] / h[s].iloc[0]) / years) for s in h.columns}
    out["US"] = float(np.mean(list(out.values())))
    return out


def flat_macro(actual: multistate.Macro, as_of: str, months: int, forecast: int = 24, reversion: int = 12,
               start: str = LONG_RUN_START) -> multistate.Macro:  # fmt: skip
    """History up to `as_of`, then: unemployment and the mortgage rate held at their `as_of` values, and each
    state's house prices growing at their trailing 12-month rate, for `forecast` months; then a straight-line
    reversion over `reversion` months to long-run averages from `start` to `as_of` (levels for unemployment
    and the rate, growth for house prices); then held there. Nothing after `as_of` is used."""
    import pandas as pd

    fut = _months_after(as_of, months)
    ramp = np.concatenate([np.zeros(forecast), np.arange(1, reversion + 1) / reversion,
                           np.ones(max(months - forecast - reversion, 0))])[:months]  # fmt: skip

    def levels(table: Any) -> Any:
        hist = table[table.index <= as_of]
        lr = hist[hist.index >= start].mean()
        now = hist.loc[as_of]
        path = np.outer(1 - ramp, now.to_numpy(dtype=float)) + np.outer(ramp, lr.to_numpy(dtype=float))
        return pd.concat([hist, pd.DataFrame(path, index=fut, columns=hist.columns)])

    hist = actual.hpi[actual.hpi.index <= as_of]
    g_now = np.log(hist.loc[as_of] / hist.loc[multistate.add_months(as_of, -12)]).to_numpy(dtype=float) / 12.0
    lr = long_run_growth(actual, as_of, start)
    g_lr = np.array([lr[str(s)] for s in hist.columns]) / 12.0
    g = np.outer(1 - ramp, g_now) + np.outer(ramp, g_lr)
    path = np.exp(np.log(hist.loc[as_of].to_numpy(dtype=float)) + np.cumsum(g, axis=0))
    hpi = pd.concat([hist, pd.DataFrame(path, index=fut, columns=hist.columns)])
    rate_hist = actual.mortgage_rate[actual.mortgage_rate.index <= as_of]
    rate_lr = float(rate_hist[rate_hist.index >= start].mean())
    rate = pd.concat(
        [rate_hist, pd.Series((1 - ramp) * float(rate_hist.loc[as_of]) + ramp * rate_lr, index=fut)]
    )
    return multistate.Macro(hpi, levels(actual.unemployment), rate)


def counted_to_censoring(p: np.ndarray, known_months: np.ndarray) -> np.ndarray:
    """Per loan, the projected chances over the first `known_months` months only (the months whose outcome
    is known: all of them for a loan that defaulted, paid off or survived the window; up to censoring
    otherwise). L-039: never cut at the actual exit."""
    k = np.arange(p.shape[1])[None, :]
    return np.where(k < np.asarray(known_months)[:, None], p, 0.0).sum(axis=1)


def _ratio(projected: float, actual: float) -> float:
    return projected / actual if actual > 0 else math.inf


def _within(r: float, band: tuple[float, float]) -> bool:
    return band[0] <= r <= band[1]


def _closer(r_model: float, r_bench: float) -> bool:
    """Model closer to 1 than the benchmark, as distance in log terms."""
    return abs(math.log(r_model)) < abs(math.log(r_bench)) if r_model > 0 and r_bench > 0 else False


def crisis_verdict(defaults: tuple[float, float], loss: tuple[float, float], bench_defaults: tuple[float, float],
                   bench_loss: tuple[float, float]) -> dict[str, Any]:  # fmt: skip
    """CB pass rule: (1) defaults in the bar, (2) loss in the bar, (3) closer than the benchmark on both.
    Each argument is (projected, actual)."""
    r_d, r_l = _ratio(*defaults), _ratio(*loss)
    b_d, b_l = _ratio(*bench_defaults), _ratio(*bench_loss)
    checks = {"defaults_in_bar": _within(r_d, BAR), "loss_in_bar": _within(r_l, BAR),
              "closer_than_benchmark": _closer(r_d, b_d) and _closer(r_l, b_l)}  # fmt: skip
    return {"defaults_ratio": r_d, "loss_ratio": r_l, "benchmark_defaults_ratio": b_d, "benchmark_loss_ratio": b_l,
            **checks, "passes": all(checks.values())}  # fmt: skip


def rolling_verdict(per_date: Mapping[str, tuple[float, float]]) -> dict[str, Any]:
    """RB pass rule on one economy: (1) pooled projected / actual 36-month loss in the bar, (2) at least 8 of
    the 11 dates inside the band. `per_date` maps a date to (projected, actual)."""
    ratios = {d: _ratio(p, a) for d, (p, a) in per_date.items()}
    pooled = _ratio(sum(p for p, _ in per_date.values()), sum(a for _, a in per_date.values()))
    inside = sum(_within(r, BAND) for r in ratios.values())
    needed = 8 if len(per_date) == 11 else math.ceil(8 / 11 * len(per_date))
    checks = {"pooled_in_bar": _within(pooled, BAR), "enough_dates_in_band": inside >= needed}
    return {"pooled_ratio": pooled, "ratios": ratios, "dates_in_band": inside, "dates_needed": needed, **checks,
            "passes": all(checks.values())}  # fmt: skip


def forbearance_verdict(booked: tuple[float, float], flag_ignored: tuple[float, float], extra_defaults: int,
                        extra_cured_12m: int) -> dict[str, Any]:  # fmt: skip
    """FT pass rule: the booked definition (A) is supported if (1) its projected / actual 36-month loss ratio
    is closer to 1 than the flag-ignored definition's (B), and (2) at least half of the defaults counted only
    under (B) were current again within 12 months."""
    r_a, r_b = _ratio(*booked), _ratio(*flag_ignored)
    share = extra_cured_12m / extra_defaults if extra_defaults else math.nan
    checks = {"booked_closer": _closer(r_a, r_b), "half_cured": bool(extra_defaults) and share >= 0.5}
    return {"booked_ratio": r_a, "flag_ignored_ratio": r_b, "extra_defaults": extra_defaults,
            "extra_cured_within_12m": extra_cured_12m, "cured_share": share, **checks, "passes": all(checks.values())}  # fmt: skip


def yearly(values: np.ndarray, months: Sequence[str]) -> dict[str, float]:
    """Monthly totals summed by calendar year."""
    out: dict[str, float] = {}
    for v, m in zip(values, months, strict=True):
        out[m[:4]] = out.get(m[:4], 0.0) + float(v)
    return out
