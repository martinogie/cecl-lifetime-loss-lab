"""Phase 4: size the L-038 overlay on expected loss (PD x LGD x EAD vs actual), for Martins's review.

    CECL_ENV=local python -m scripts.size_overlay_v2 [--write]

Inputs (git-ignored, loan-level, never committed):
- data/local/v2/holdout_202407_p_default.parquet: v2's projected default chance per loan and month for
  the 1 July 2024 book (`databricks/notebooks/63_overlay_inputs_v2.py`, which reproduces the holdout
  projection exactly);
- the Phase 3 LGD model and EAD rates (data/local/phase3/), the LGD default events (actual defaults and
  how they ended), the Freddie origination files and the state house-price index.

What it computes (24 months, July 2024 - June 2026, the economy as it happened):
1. **Projected expected loss**: sum over loans and months of PD x EAD x LGD, where EAD is the payment
   schedule plus partial prepayment (cecl/ead.py) and LGD is the Phase 3 model with LTV and the 12-month
   house-price change of that month.
2. **Actual defaults at the model's LGD**: the same loans' real defaults in the window, each priced at
   its balance at default x the Phase 3 LGD for its inputs at default. Projected (1) / this (2) is the
   **PD gap in loss terms** (what the overlay for L-038 must cover, before any LGD effect).
3. **The LGD conservatism offset (L-033)**: the Phase 3 LGD over-predicts loss per default since 2017
   (resolved 2017-2021 defaults: 0.072 predicted vs 0.044 actual). Shown separately, never silently
   netted: (2) divided by that ratio is an estimate of the real losses on the actual defaults.
4. The ADR-006 cap check: the overlay as a share of the modelled expected loss, against 5% per overlay
   and 10% in total.
Writes docs/phase4/overlay_sizing_L038.json (aggregates only) with `--write`.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any

import numpy as np

from cecl import ead, environment, lgd
from cecl.models import multistate

LOCAL = Path("data/local")
PD_FILE = LOCAL / "v2" / "holdout_202407_p_default.parquet"
OUT = Path("docs/phase4/overlay_sizing_L038.json")
START, MONTHS = "202407", 24
CAP_EACH, CAP_TOTAL = 0.05, 0.10  # ADR-006
# L-033 (docs/phase3/results.json, "2017-2021 (resolved, fair check)"): balance-weighted LGD per default
LGD_CHECK: dict[str, Any] = {"predicted": 0.072, "actual": 0.044, "defaults": 4238, "period": "defaults 2017-2021, resolved"}


def hpi_table(con: Any) -> Any:
    path = LOCAL / "silver" / "macro" / "state_monthly.parquet"
    df = con.sql(f"select state, strftime(month, '%Y%m') as ym, value from read_parquet('{path}') "
                 "where variable = 'hpi'").df()  # fmt: skip
    return df.pivot_table(index="ym", columns="state", values="value")


def lgd_inputs(book: Any, hpi: Any, ym: str, balance: np.ndarray) -> np.ndarray:
    """The Phase 3 LGD inputs if a loan defaulted in month `ym` with `balance` owed."""
    states = book["property_state"].astype(str).to_numpy()
    h_now = hpi.loc[ym].reindex(states).to_numpy(dtype=float)
    h_12 = hpi.loc[multistate.add_months(ym, -12)].reindex(states).to_numpy(dtype=float)
    value = book["orig_upb"].to_numpy(dtype=float) / (book["orig_ltv"].to_numpy(dtype=float) / 100.0)
    f = {"ltv_default": 100.0 * balance / (value * h_now / book["hpi_orig"].to_numpy(dtype=float)),
         "hpi_change_12": h_now / h_12 - 1.0, "mi_pct": book["mi_pct"].to_numpy(dtype=float),
         "judicial": book["property_state"].isin(lgd.JUDICIAL).to_numpy(dtype=float),
         "log_orig_upb": np.log(book["orig_upb"].to_numpy(dtype=float)),
         "credit_score": book["credit_score"].to_numpy(dtype=float), "investor": book["investor"].to_numpy(dtype=float)}  # fmt: skip
    return np.column_stack([f[c] for c in lgd.FEATURES])


def gap_rows(proj_el: np.ndarray, act_el: np.ndarray, groups: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for g in sorted(set(groups.tolist())):
        m = groups == g
        a, p = float(act_el[m].sum()), float(proj_el[m].sum())
        rows.append({"group": str(g), "projected_el": p, "actual_defaults_at_model_lgd": a,
                     "ratio": p / a if a else float("nan")})  # fmt: skip
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    environment.current_env()
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    book = pd.read_parquet(PD_FILE)
    orig = con.sql("select loan_id, cast(coalesce(mi_pct, 0) as double) as mi_pct, "
                   "case when occupancy_status = 'I' then 1.0 else 0.0 end as investor "
                   f"from read_parquet('{LOCAL}/bronze/freddie_orig/**/*.parquet', hive_partitioning=true)").df()  # fmt: skip
    book = book.merge(orig, on="loan_id", how="left")
    book[["mi_pct", "investor"]] = book[["mi_pct", "investor"]].fillna(0.0)
    with open(LOCAL / "phase3" / "lgd_model.pkl", "rb") as fh:
        model = pickle.load(fh)
    curtail = {int(k): v for k, v in json.loads((LOCAL / "phase3" / "ead_rates.json").read_text()).items()}
    hpi = hpi_table(con)

    # 1. projected expected loss, loan by month
    months = [multistate.add_months(START, k) for k in range(MONTHS)]
    pd_m = book[[f"pd_{m}" for m in months]].to_numpy(dtype=float)
    proj_el = np.zeros(len(book))
    proj_by_month: list[dict[str, Any]] = []
    for k, ym in enumerate(months):
        bal = ead.project(
            book["upb_start"], book["rate_start"], book["remaining_start"], book["loan_age"], k, curtail
        )
        lgd_k = model.predict(np.nan_to_num(lgd_inputs(book, hpi, ym, bal), nan=0.0))
        el_k = pd_m[:, k] * bal * lgd_k
        proj_el += el_k
        proj_by_month.append({"month": ym, "pd": float(pd_m[:, k].sum()), "ead_weighted_lgd": float((pd_m[:, k] * bal * lgd_k).sum() / max((pd_m[:, k] * bal).sum(), 1e-9)),
                              "expected_loss": float(el_k.sum())})  # fmt: skip

    # 2. actual defaults of the same loans in the window, priced at the model's LGD
    ev = pd.read_parquet(LOCAL / "phase3" / "lgd_events.parquet")
    ev = ev[(ev.default_ym >= START) & (ev.default_ym <= months[-1]) & ev.loan_id.isin(book.loan_id)].copy()
    usable = ev.dropna(subset=["ltv_default", "hpi_change_12"])
    ev["model_lgd"] = np.nan
    ev.loc[usable.index, "model_lgd"] = model.predict(lgd.matrix(usable))
    fill = float(np.nanmean(ev["model_lgd"]))  # the few defaults with a missing input get the average LGD
    ev["model_lgd"] = ev["model_lgd"].fillna(fill)
    ev["act_el"] = ev["default_upb"] * ev["model_lgd"]
    act_el = book["loan_id"].map(ev.set_index("loan_id")["act_el"]).fillna(0.0).to_numpy()
    resolved = ev[ev.severity.notna()]
    sev = np.clip(resolved.severity.to_numpy(dtype=float), *lgd.SEVERITY_CAP)
    upb_r = resolved.default_upb.to_numpy(dtype=float)

    P, A = float(proj_el.sum()), float(act_el.sum())
    lgd_ratio = LGD_CHECK["predicted"] / LGD_CHECK["actual"]
    gross_uplift = A / P - 1.0
    real_loss_est = A / lgd_ratio
    net_uplift = real_loss_est / P - 1.0
    vint = book["vintage_year"].astype(int).to_numpy()
    vgroup = np.where(vint >= 2022, "2022-2024", np.where(vint >= 2019, "2019-2021", "2018 and older"))
    res: dict[str, Any] = {
        "note": "Aggregates only. 24 months from 1 July 2024, the economy as it happened. Educational model on Freddie "
        "Mac conforming mortgages; not an audited reserve.",
        "book": {"loans": int(len(book)), "balance": float(book["upb_start"].sum())},
        "defaults": {
            "projected": float(pd_m.sum()),
            "actual_in_window": int(len(ev)),
            "count_ratio": float(pd_m.sum() / len(ev)),
        },
        "expected_loss": {
            "projected_v2": P,
            "actual_defaults_at_model_lgd": A,
            "pd_gap_ratio": P / A,
            "gross_uplift_needed": gross_uplift,
        },
        "lgd_offset_L033": {
            "check": LGD_CHECK,
            "model_over_actual": lgd_ratio,
            "estimated_real_loss_on_actual_defaults": real_loss_est,
            "projected_over_estimated_real_loss": P / real_loss_est,
            "net_uplift_needed": net_uplift,
            "holdout_resolved_defaults_information_only": {
                "defaults": int(len(ev)),
                "resolved": int(len(resolved)),
                "resolved_actual_lgd": float((sev * upb_r).sum() / upb_r.sum()) if len(resolved) else None,
                "resolved_model_lgd": float(
                    (resolved.default_upb * ev.loc[resolved.index, "model_lgd"]).sum() / upb_r.sum()
                )
                if len(resolved)
                else None,
                "outcomes": {str(k): int(v) for k, v in ev.outcome.value_counts().items()},
                "why_not_used": "most holdout defaults are unresolved; the resolved ones are the quick cures and payoffs, so their LGD is biased low",
            },
        },  # fmt: skip
        "cap_check_ADR006": {
            "cap_each": CAP_EACH,
            "cap_total": CAP_TOTAL,
            "gross_uplift": gross_uplift,
            "gross_within_cap": gross_uplift <= CAP_EACH,
            "net_uplift": net_uplift,
            "net_within_cap": net_uplift <= CAP_EACH,
        },  # fmt: skip
        "by_vintage_group": gap_rows(proj_el, act_el, vgroup),
        "by_starting_state": gap_rows(proj_el, act_el, book["state_prev"].astype(int).astype(str).to_numpy()),
        "by_year_of_default": [],
        "by_month": proj_by_month,
    }
    for y in ("2024", "2025", "2026"):
        p = float(sum(r["expected_loss"] for r in proj_by_month if r["month"].startswith(y)))
        a = float(ev.loc[ev.default_ym.str.startswith(y), "act_el"].sum())
        res["by_year_of_default"].append(
            {"year": y, "projected_el": p, "actual_defaults_at_model_lgd": a, "ratio": p / a if a else None}
        )
    print(json.dumps({k: v for k, v in res.items() if k not in ("by_month",)}, indent=1, default=float))
    if args.write:
        OUT.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
