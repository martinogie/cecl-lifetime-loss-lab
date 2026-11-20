"""Economic scenario paths by state for the CECL engine (ADR-003, ADR-005, ADR-008 with option B4b).

For a reserve month R (the last month of real data the reserve uses, for example June 2026):

1. **National paths (ADR-003 as amended, ADR-005).** The Fed's quarterly changes (from its own starting
   point, `fed_change` in silver `fed_paths`: points for rates and unemployment, a ratio for house prices)
   land on the quarters after R: horizon h is the h-th quarter after R. Each change sits on its quarter's
   middle month and is interpolated linearly from 0 at R (rule M1, so history and forecast join without a
   seam). The adverse path is the halfway path already in silver.
2. **State paths (ADR-008).** Each state's change = beta x the national change. The betas are slopes of the
   state's year-over-year change on the national one, quarterly, 1991 to R (unemployment in points, house
   prices in log change), capped at 0.25-2.0 (unemployment) and 0.25-3.0 (house prices). **B4b:** the
   betas are then divided by their book-balance-weighted average, so the book's average state change equals
   the Fed's national change exactly. The mortgage rate is national.
3. **Forecast and reversion (ADR-003).** The paths run for 24 months after R; over the next 12 months each
   state's unemployment moves in a straight line to its own long-run average (1990 onward), its monthly
   house-price growth moves in a straight line to its long-run growth, and the mortgage rate to its long-run
   average. After month 36 the long-run values hold (house prices grow at the long-run rate).

Values are anchored on the real values in month R. Educational model on Freddie Mac conforming mortgages;
not an audited reserve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from cecl.models import multistate

FORECAST_MONTHS = 24  # ADR-003 A
REVERSION_MONTHS = 12  # ADR-003 B
BETA_START = "1991"  # ADR-008 B1(b): 1991 to the latest quarter
CAPS = {"unemployment_rate": (0.25, 2.0), "hpi": (0.25, 3.0)}  # ADR-008 B3
STATE_VARIABLES = ("unemployment_rate", "hpi")
SCENARIOS = ("baseline", "adverse", "severely_adverse")
# Point in time (Martins, 20 November 2026): a reserve uses the latest Fed scenarios published by its reserve
# date. 2025 scenarios: 4 February 2025; 2026 scenarios: February 2026.
FED_PUBLISHED = {2025: "202502", 2026: "202602"}  # scenario year -> publication month (YYYYMM)
FED_TABLES = {2025: "fed_paths_2025", 2026: "fed_paths"}  # silver table per scenario year


@dataclass(frozen=True)
class Shock:
    """A custom shock on top of a Fed path (Phase 5, Martins 20 November 2026): house prices x (1 + hpi_pct),
    unemployment + unemployment_pts, mortgage rate + rate_pts, reached in a straight line over `ramp_months`
    (12 = 4 quarters; 1 = an immediate jump in the first month), held to the end of the forecast, then the
    usual reversion (ADR-003) from the shocked level. House prices revert in growth, so their level stays
    lower; unemployment and the rate revert to their long-run levels."""

    hpi_pct: float = 0.0
    unemployment_pts: float = 0.0
    rate_pts: float = 0.0
    ramp_months: int = 12

    def ramp(self, months: int) -> np.ndarray:
        """Share of the shock in place in months 1..months after the reserve date."""
        return np.minimum(np.arange(1, months + 1) / self.ramp_months, 1.0)


SHOCKS = {  # all on the Fed baseline
    "hpi_m10": Shock(hpi_pct=-0.10),
    "hpi_m20": Shock(hpi_pct=-0.20),
    "hpi_m30": Shock(hpi_pct=-0.30),
    "unemployment_p2": Shock(unemployment_pts=2.0),
    "unemployment_p4": Shock(unemployment_pts=4.0),
    "rates_up2": Shock(rate_pts=2.0),
    "rates_down2": Shock(rate_pts=-2.0),
    "hpi_m20_unemployment_p2": Shock(hpi_pct=-0.20, unemployment_pts=2.0),
    "hpi_m30_unemployment_p4": Shock(hpi_pct=-0.30, unemployment_pts=4.0),
    "hpi_m30_immediate": Shock(hpi_pct=-0.30, ramp_months=1),  # information only
}


def fed_year(reserve_ym: str) -> int:
    """The Fed scenario year a reserve dated `reserve_ym` uses: the latest one published by then."""
    known = [y for y, pub in FED_PUBLISHED.items() if pub <= reserve_ym]
    if not known:
        raise ValueError(f"no Fed scenarios were published by {reserve_ym}")
    return max(known)


def ym(d: Any) -> str:
    return f"{d.year}{d.month:02d}" if hasattr(d, "year") else str(d).replace("-", "")[:6]


def wide(state_monthly: Any, variable: str) -> Any:
    """state x month table (index YYYYMM) of one variable from silver macro_state_monthly."""
    s = state_monthly[state_monthly["variable"] == variable].copy()
    s["ym"] = s["month"].map(ym)
    return s.pivot_table(index="ym", columns="state", values="value").sort_index()


def national(national_monthly: Any, variable: str) -> Any:
    n = national_monthly[national_monthly["variable"] == variable].copy()
    n["ym"] = n["month"].map(ym)
    return n.set_index("ym")["value"].astype(float).sort_index()


def _yoy(series: Any, log: bool) -> Any:
    """Year-over-year change of quarter-end months (March, June, September, December)."""
    q = series[[i for i in series.index if i[4:] in ("03", "06", "09", "12")]]
    vals = np.log(q.astype(float)) if log else q.astype(float)
    return vals - vals.shift(4)


def betas(state_tbl: Any, nat: Any, variable: str, end: str, start: str = BETA_START) -> dict[str, float]:
    """Capped slope of each state's year-over-year change on the national change, quarterly, start to end."""
    log = variable == "hpi"
    x = _yoy(nat[(nat.index >= f"{int(start) - 1}01") & (nat.index <= end)], log)
    out: dict[str, float] = {}
    lo, hi = CAPS[variable]
    for st in state_tbl.columns:
        y = _yoy(state_tbl[st][(state_tbl.index >= f"{int(start) - 1}01") & (state_tbl.index <= end)], log)
        both = (~x.isna()) & (~y.reindex(x.index).isna())
        xs, ys = x[both].to_numpy(dtype=float), y.reindex(x.index)[both].to_numpy(dtype=float)
        if len(xs) < 8 or np.var(xs) == 0:
            out[st] = 1.0
            continue
        slope = float(np.cov(xs, ys, bias=True)[0, 1] / np.var(xs))
        out[st] = float(np.clip(slope, lo, hi))
    return out


def rescale(beta: dict[str, float], weights: dict[str, float]) -> dict[str, float]:
    """B4b: divide by the book-balance-weighted average beta (states without book balance carry weight 0)."""
    total = sum(weights.get(s, 0.0) for s in beta)
    avg = sum(beta[s] * weights.get(s, 0.0) for s in beta) / total
    return {s: b / avg for s, b in beta.items()}


def national_changes(
    fed_paths: Any, scenario: str, variable: str, months: int = FORECAST_MONTHS + 3
) -> np.ndarray:
    """Monthly national change after R (index 0 = R, change 0): points, or log ratio for house prices."""
    f = fed_paths[(fed_paths["scenario"] == scenario) & (fed_paths["variable"] == variable)].sort_values(
        "horizon"
    )
    change = f["fed_change"].to_numpy(dtype=float)
    if variable == "hpi":
        change = np.log(change)
    mid = 3 * f["horizon"].to_numpy(dtype=float) - 1.0  # quarter h's middle month, counted from R
    return np.interp(np.arange(months + 1), np.concatenate([[0.0], mid]), np.concatenate([[0.0], change]))


@dataclass
class Scenario:
    name: str
    reserve_ym: str
    macro: multistate.Macro  # history to R plus the scenario after it, `horizon` months
    betas: dict[str, dict[str, float]]  # variable -> state -> rescaled beta (B4b)
    long_run: dict[str, dict[str, float]]  # variable -> state (or "US") -> long-run value
    months: list[str]

    def table(self) -> Any:
        """The future paths as a tidy table (state, ym, variable, value): aggregates, safe to publish."""
        import pandas as pd

        rows = []
        for var, tbl in (("hpi", self.macro.hpi), ("unemployment_rate", self.macro.unemployment)):
            fut = tbl[tbl.index > self.reserve_ym]
            for st in fut.columns:
                rows += [
                    {"state": st, "ym": i, "variable": var, "value": float(v)} for i, v in fut[st].items()
                ]
        rate = self.macro.mortgage_rate[self.macro.mortgage_rate.index > self.reserve_ym]
        rows += [
            {"state": "US", "ym": i, "variable": "mortgage_rate_30y", "value": float(v)}
            for i, v in rate.items()
        ]
        return pd.DataFrame(rows)


def long_run_values(long_run: Any) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {"unemployment_rate": {}, "hpi_growth": {}, "mortgage_rate_30y": {}}
    for r in long_run.itertuples():
        if r.variable == "unemployment_rate":
            out["unemployment_rate"][r.geo] = float(r.value)
        elif r.variable == "hpi":
            out["hpi_growth"][r.geo] = float(r.value)  # annual log growth
        elif r.variable == "mortgage_rate_30y":
            out["mortgage_rate_30y"][r.geo] = float(r.value)
    return out


def build(scenario: str, reserve_ym: str, state_monthly: Any, national_monthly: Any, fed_paths: Any, long_run: Any,
          weights: dict[str, float], horizon: int, forecast_months: int = FORECAST_MONTHS,
          reversion_months: int = REVERSION_MONTHS, shock: Shock | None = None) -> Scenario:  # fmt: skip
    """The scenario's economy for a reserve month: history up to `reserve_ym`, then `horizon` months.

    `forecast_months` (reasonable-and-supportable period) and `reversion_months` (straight-line reversion to the
    long-run values) are settings; the defaults are ADR-003's 24 and 12. The forecast cannot run past the end of
    the Fed's paths (no made-up quarters), and reversion must be at least one month. `shock` (Phase 5) is
    added to the Fed path during the forecast, before the reversion.
    """
    import pandas as pd

    f, rv = int(forecast_months), int(reversion_months)
    fed_months = 3 * int(fed_paths["horizon"].max()) - 1  # the last Fed quarter's middle month
    if not 1 <= f <= fed_months:
        raise ValueError(f"forecast_months must be 1-{fed_months} (the Fed's paths end there), got {f}")
    if rv < 1:
        raise ValueError(f"reversion_months must be at least 1, got {rv}")
    shock = shock or Shock()
    ramp = shock.ramp(f)
    lr = long_run_values(long_run)
    months = [multistate.add_months(reserve_ym, k) for k in range(1, horizon + 1)]
    tables, rescaled = {}, {}
    for var in STATE_VARIABLES:
        hist = wide(state_monthly, var)
        hist = hist[hist.index <= reserve_ym]
        b = rescale(betas(hist, national(national_monthly, var), var, reserve_ym), weights)
        rescaled[var] = b
        nat = national_changes(fed_paths, scenario, var, f + 3)  # index 0..f+3
        anchor = hist.loc[reserve_ym]
        fut = np.zeros((horizon, len(hist.columns)))
        for j, st in enumerate(hist.columns):
            a, beta = float(anchor[st]), b[st]
            if var == "unemployment_rate":
                path = a + beta * nat[1 : f + 1] + shock.unemployment_pts * ramp
                target = lr["unemployment_rate"].get(st, lr["unemployment_rate"]["US"])
                rev = path[-1] + (target - path[-1]) * np.arange(1, rv + 1) / rv
                tail = np.full(max(horizon - f - rv, 0), target)
                series = np.concatenate([path, rev, tail])
            else:
                logp = np.log(a) + beta * nat[: f + 1]  # index 0 = R
                logp[1:] += np.log1p(shock.hpi_pct) * ramp
                g_end = logp[-1] - logp[-2]
                g_lr = lr["hpi_growth"].get(st, lr["hpi_growth"]["US"]) / 12.0
                g = g_end + (g_lr - g_end) * np.arange(1, rv + 1) / rv
                g = np.concatenate([g, np.full(max(horizon - f - rv, 0), g_lr)])
                series = np.exp(np.concatenate([logp[1:], logp[-1] + np.cumsum(g)]))
            fut[:, j] = np.maximum(series[:horizon], 0.1 if var == "unemployment_rate" else 1e-6)
        tables[var] = pd.concat([hist, pd.DataFrame(fut, index=months, columns=hist.columns)])
    rate_hist = national(national_monthly, "mortgage_rate_30y")
    rate_hist = rate_hist[rate_hist.index <= reserve_ym]
    a = float(rate_hist.loc[reserve_ym])
    path = (
        a
        + national_changes(fed_paths, scenario, "mortgage_rate_30y", f + 3)[1 : f + 1]
        + shock.rate_pts * ramp
    )
    target = lr["mortgage_rate_30y"]["US"]
    rev = path[-1] + (target - path[-1]) * np.arange(1, rv + 1) / rv
    rate = np.concatenate([path, rev, np.full(max(horizon - f - rv, 0), target)])[:horizon]
    macro = multistate.Macro(tables["hpi"], tables["unemployment_rate"],
                             pd.concat([rate_hist, pd.Series(rate, index=months)]))  # fmt: skip
    return Scenario(scenario, reserve_ym, macro, rescaled, lr, months)


def weighted_change(sc: Scenario, weights: dict[str, float], variable: str, k: int) -> float:
    """The book-balance-weighted state change k months after R (for the B4b check)."""
    tbl = sc.macro.hpi if variable == "hpi" else sc.macro.unemployment
    r, m = tbl.loc[sc.reserve_ym], tbl.loc[sc.months[k - 1]]
    total = sum(weights.get(s, 0.0) for s in tbl.columns)
    if variable == "hpi":
        return float(sum(weights.get(s, 0.0) * np.log(m[s] / r[s]) for s in tbl.columns) / total)
    return float(sum(weights.get(s, 0.0) * (m[s] - r[s]) for s in tbl.columns) / total)
