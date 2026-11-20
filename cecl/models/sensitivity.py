"""Model v2's downturn-sensitivity check (ADR-009 Amendment 4; thresholds approved 19 November 2026).

The final v2 fit plays the book alive on 1 July 2022 forward 36 months with the path simulation, once
with the economy held at its June 2022 level (the **baseline**) and once per shock. Each shock builds in
a straight line over the first 12 months and is then held:

- house prices -10 / -20 / -30% (every state's index multiplied by 0.90 / 0.80 / 0.70);
- unemployment +2 / +4 points (every state);
- combined: house prices -30% and unemployment +4 points together.

Every scenario uses the same random seed, so scenarios differ only by the economy. R = projected defaults
under a shock / the same loans' projected defaults under the baseline. The pass tests (all must hold) are
in `evaluate`; they were fixed before this code was written.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from cecl.models import multistate

START_YM = "202207"
MONTHS = 36
RAMP = 12  # months for a shock to reach full size
LTV_MONTH = 12  # test 7(a): current LTV measured in month 12, when the shock is at full size


@dataclass(frozen=True)
class Shock:
    name: str
    hpi_fall: float  # 0.30 = house prices 30% lower
    ur_rise: float  # unemployment points added


BASELINE = Shock("baseline", 0.0, 0.0)
SHOCKS = (
    Shock("hpi_-10", 0.10, 0.0), Shock("hpi_-20", 0.20, 0.0), Shock("hpi_-30", 0.30, 0.0),
    Shock("ur_+2", 0.0, 2.0), Shock("ur_+4", 0.0, 4.0), Shock("combined", 0.30, 4.0),
)  # fmt: skip
ALL = (BASELINE, *SHOCKS)

# Thresholds (ADR-009 Amendment 4, approved before this code was written)
STEP_RISE = 0.02  # test 1: each step at least 2% above the one before
LADDERS = (("baseline", "hpi_-10", "hpi_-20", "hpi_-30"), ("baseline", "ur_+2", "ur_+4"))
FLOORS = {"hpi_-20": 1.10, "hpi_-30": 1.25, "ur_+2": 1.10, "ur_+4": 1.25}  # tests 2 and 3
CAP = 10.0  # test 4
COMBINED_FLOOR = 1.50  # test 6
ORDERING_SHOCKS = ("hpi_-30", "ur_+4")  # test 7
LTV_CUT = 80.0
SCORE_LOW, SCORE_HIGH = 680.0, 740.0
TOLERANCE = 1e-9  # test 5 tie-out


def ramp(ym: str, start_ym: str = START_YM, months: int = RAMP) -> float:
    """Share of the shock in force in month `ym`: 1/12 in the start month, full from month 12 on."""
    m = multistate.months_between(start_ym, ym)
    return float(min(max(m, 0), months) / months)


def scenario(macro: multistate.Macro, shock: Shock, start_ym: str = START_YM, months: int = MONTHS) -> multistate.Macro:  # fmt: skip
    """The economy for one scenario: actual history up to the month before the start, then every input
    held at that month's level, with the shock built in over `RAMP` months."""
    import pandas as pd

    base_ym = multistate.add_months(start_ym, -1)
    future = [multistate.add_months(start_ym, k) for k in range(months + 1)]
    share = np.array([ramp(ym, start_ym) for ym in future])

    def extend(table: Any, values: np.ndarray) -> Any:
        past = table[table.index < start_ym]
        if isinstance(table, pd.Series):
            return pd.concat([past, pd.Series(values, index=future, name=table.name)])
        return pd.concat([past, pd.DataFrame(values, index=future, columns=table.columns)])

    hpi0 = macro.hpi.loc[base_ym].to_numpy(dtype=float)
    ur0 = macro.unemployment.loc[base_ym].to_numpy(dtype=float)
    hpi = np.outer(1.0 - shock.hpi_fall * share, hpi0)
    ur = ur0[None, :] + shock.ur_rise * share[:, None]
    rate = np.full(len(future), float(macro.mortgage_rate.loc[base_ym]))
    return multistate.Macro(
        extend(macro.hpi, hpi), extend(macro.unemployment, ur), extend(macro.mortgage_rate, rate)
    )


def tie_out(start: Any, base: multistate.Macro, shocked: multistate.Macro, shock: Shock,
            start_ym: str = START_YM, months: int = MONTHS) -> dict[str, float]:  # fmt: skip
    """Test 5: the house-price and unemployment inputs each loan gets in every projected month differ from
    the baseline by exactly the shock (largest error found; 0 means exact)."""
    states = start["property_state"].to_numpy().astype(str)
    hpi_err = ur_err = 0.0
    for k in range(1, months):
        prev = multistate.add_months(start_ym, k - 1)  # the month whose economy month k's inputs use
        s = ramp(prev, start_ym)
        h0, h1 = base.by_state(base.hpi, prev, states), shocked.by_state(shocked.hpi, prev, states)
        u0, u1 = (
            base.by_state(base.unemployment, prev, states),
            shocked.by_state(shocked.unemployment, prev, states),
        )
        hpi_err = max(hpi_err, float(np.nanmax(np.abs(h1 / h0 - (1.0 - shock.hpi_fall * s)))))
        ur_err = max(ur_err, float(np.nanmax(np.abs((u1 - u0) - shock.ur_rise * s))))
    return {"hpi_max_error": hpi_err, "unemployment_max_error": ur_err}


def ltv_after(
    start: Any, macro: multistate.Macro, start_ym: str = START_YM, k: int = LTV_MONTH
) -> np.ndarray:
    """Each loan's current LTV in projected month `k` of a scenario (scheduled balance / shocked value)."""
    x = multistate.month_inputs(start, macro, start_ym, k, ("loan_age", "calendar_month"))
    return np.asarray(x["current_ltv"], dtype=float)


def groups(start: Any, ltv_by_shock: dict[str, np.ndarray], top_states: int = 10) -> dict[str, dict[str, np.ndarray]]:  # fmt: skip
    """Masks of loans for the per-group R: risk groups for test 7, vintages and the largest states."""
    score = start["credit_score"].to_numpy(dtype=float)
    out: dict[str, dict[str, np.ndarray]] = {
        "credit_score": {"below_680": score < SCORE_LOW, "740_plus": score >= SCORE_HIGH},
        "starting_state": {
            str(s): start["state_prev"].to_numpy() == s for s in sorted(start["state_prev"].unique())
        },
        "vintage": {
            str(v): start["vintage_year"].to_numpy() == v for v in sorted(start["vintage_year"].unique())
        },
    }
    biggest = start["property_state"].value_counts().head(top_states).index
    out["state"] = {str(s): start["property_state"].to_numpy() == s for s in biggest}
    for name, ltv in ltv_by_shock.items():
        out[f"ltv_after_{name}"] = {"above_80": ltv > LTV_CUT, "80_or_below": ltv <= LTV_CUT}
    return out


def ratios(defaults: dict[str, np.ndarray], masks: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    """R overall and per group for every shock; `defaults[scenario]` is each loan's 36-month projected
    defaults."""
    base = defaults["baseline"]
    out: dict[str, Any] = {"totals": {s: float(v.sum()) for s, v in defaults.items()}, "R": {}, "groups": {}}
    for s, v in defaults.items():
        out["R"][s] = float(v.sum() / base.sum())
    for gname, gmasks in masks.items():
        out["groups"][gname] = {}
        for label, mask in gmasks.items():
            b = float(base[mask].sum())
            out["groups"][gname][label] = {
                "loans": int(mask.sum()), "baseline_defaults": b,
                "R": {s: (float(v[mask].sum()) / b if b > 0 else float("nan")) for s, v in defaults.items() if s != "baseline"},
            }  # fmt: skip
    return out


def evaluate(r: dict[str, Any], mechanics: dict[str, Any]) -> dict[str, Any]:
    """Tests 1-7 of ADR-009 Amendment 4 on the output of `ratios` and the mechanics checks."""
    R, G = r["R"], r["groups"]
    tests: dict[str, dict[str, Any]] = {}
    steps = []
    for ladder in LADDERS:
        for a, b in zip(ladder, ladder[1:], strict=False):
            steps.append(
                {"from": a, "to": b, "rise": R[b] / R[a] - 1.0, "passes": R[b] >= R[a] * (1.0 + STEP_RISE)}
            )
    tests["1_monotonic"] = {"steps": steps, "passes": all(s["passes"] for s in steps)}
    for t, names in (
        ("2_material_house_prices", ("hpi_-20", "hpi_-30")),
        ("3_material_unemployment", ("ur_+2", "ur_+4")),
    ):
        rows = {n: {"R": R[n], "floor": FLOORS[n], "passes": R[n] >= FLOORS[n]} for n in names}
        tests[t] = {"cells": rows, "passes": all(c["passes"] for c in rows.values())}
    tests["4_plausible"] = {"largest_R": max(R.values()), "cap": CAP, "passes": max(R.values()) <= CAP}
    hist_ok = not any(mechanics["history_check"].values())
    tie_ok = all(v <= TOLERANCE for t in mechanics["tie_out"].values() for v in t.values())
    tests["5_mechanics"] = {"history_check": mechanics["history_check"], "tie_out": mechanics["tie_out"],
                            "passes": hist_ok and tie_ok}  # fmt: skip
    c = R["combined"]
    tests["6_combined"] = {"R": c, "floor": COMBINED_FLOOR, "single_hpi_-30": R["hpi_-30"], "single_ur_+4": R["ur_+4"],
                           "passes": c >= COMBINED_FLOOR and c > R["hpi_-30"] and c > R["ur_+4"]}  # fmt: skip
    order = {}
    for s in ORDERING_SHOCKS:
        ltv = G[f"ltv_after_{s}"]
        hi_l, lo_l = ltv["above_80"]["R"][s], ltv["80_or_below"]["R"][s]
        hi_c, lo_c = G["credit_score"]["below_680"]["R"][s], G["credit_score"]["740_plus"]["R"][s]
        order[s] = {"ltv_above_80": hi_l, "ltv_80_or_below": lo_l, "ltv_passes": bool(hi_l > lo_l),
                    "score_below_680": hi_c, "score_740_plus": lo_c, "score_passes": bool(hi_c > lo_c)}  # fmt: skip
    tests["7_risk_ordering"] = {"shocks": order,
                                "passes": all(o["ltv_passes"] and o["score_passes"] for o in order.values())}  # fmt: skip
    return {"tests": tests, "passes": all(t["passes"] for t in tests.values())}
