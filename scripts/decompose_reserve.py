"""PD x LGD x EAD decomposition of the 30 June 2026 reserve, by scenario (reading only; nothing is re-priced).

    python -m scripts.decompose_reserve

Reads the priced loan files that `scripts/build_reserve.py` already wrote (data/local/reserve/priced_*.parquet,
local only) and writes aggregates to docs/phase4/reserve_components_2026Q2.json.

For each loan the engine sums, over every future month (36 monthly steps, then yearly steps to maturity):
    expected loss  EL  = sum_t  P(default in t) x balance_t x LGD_t
    lifetime PD        = sum_t  P(default in t)
    expected exposure  X  = sum_t  P(default in t) x balance_t
So, per loan and exactly:   EL = PD x (X / PD) x (EL / X)
    EAD ratio = (X / PD) / current balance   (the default-weighted balance at default, as a share of today's)
    LGD       = EL / X                       (the exposure-weighted loss per $ defaulted)
Timing and the tail are inside each loan's three numbers, so they reconcile exactly loan by loan. At book level,
the product of simple balance-weighted averages misses the cross-loan pattern (loans with higher PD also have
higher LGD), so the decomposition is shown two ways:
    1. balance-weighted averages (what Martins asked for), with the gap to the reserve shown and explained;
    2. the reconciling chain: PD weighted by balance, EAD ratio weighted by expected defaults (balance x PD),
       LGD weighted by expected exposure, whose product x balance equals the reserve to rounding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

RES = Path("data/local/reserve")
OUT = Path("docs/phase4/reserve_components_2026Q2.json")
SRC = Path("docs/phase4/reserve_2026Q2.json")
R = "202606"
WEIGHTS = {"baseline": 0.60, "adverse": 0.30, "severely_adverse": 0.10}  # ADR-005, as in build_reserve


def components(bal: Any, pd_: Any, x: Any, el: Any, pd36: Any, tail: Any) -> dict[str, Any]:
    """Book-level components from per-loan arrays (balance, lifetime PD, expected exposure, expected loss)."""
    B = float(bal.sum())
    ead_ratio = x / pd_ / bal  # per loan
    lgd_ = el / x  # per loan
    bw = {"lifetime_pd": float((pd_ * bal).sum() / B), "lgd": float((lgd_ * bal).sum() / B),
          "ead_ratio": float((ead_ratio * bal).sum() / B)}  # fmt: skip
    chain = {"lifetime_pd": float((pd_ * bal).sum() / B), "ead_ratio": float(x.sum() / (pd_ * bal).sum()),
             "lgd": float(el.sum() / x.sum())}  # fmt: skip
    reserve = float(el.sum())
    simple = bw["lifetime_pd"] * bw["lgd"] * bw["ead_ratio"] * B
    chained = chain["lifetime_pd"] * chain["ead_ratio"] * chain["lgd"] * B
    # the gap between the simple product and the reserve, split into the two weighting steps
    ead_step = (
        bw["lifetime_pd"] * chain["ead_ratio"] * bw["lgd"] * B - simple
    )  # EAD weighted by expected defaults
    lgd_step = chained - (simple + ead_step)  # LGD weighted by expected exposure
    return {
        "balance": B,
        "reserve": reserve,
        "balance_weighted": bw,
        "simple_product_x_balance": simple,
        "gap_reserve_minus_simple_product": reserve - simple,
        "gap_from_ead_weighting": ead_step,
        "gap_from_lgd_weighting": lgd_step,
        "reconciling_chain": chain,
        "chain_product_x_balance": chained,
        "chain_difference": chained - reserve,
        "lifetime_pd_within_36_months": float((pd36 * bal).sum() / B),
        "lifetime_pd_tail_after_36_months": float(((pd_ - pd36) * bal).sum() / B),
        "tail_share_of_reserve": float(tail.sum() / reserve),
    }


def main() -> int:
    import pandas as pd

    cols = ["loan_id", "upb_start", "lifetime_pd", "pd_36", "ead_at_default", "total", "tail"]
    f = {s: pd.read_parquet(RES / f"priced_{R}_{R}_{s}.parquet", columns=cols) for s in WEIGHTS}
    ids = f["baseline"]["loan_id"].to_numpy()
    for s, d in f.items():  # same loans in the same order, or the weighted column would be wrong
        if not np.array_equal(d["loan_id"].to_numpy(), ids):
            raise SystemExit(f"loan order differs in {s}")
    bal = f["baseline"]["upb_start"].to_numpy(dtype=float)
    out: dict[str, Any] = {
        "note": "Aggregates only. Reading only: from the priced files of the 30 June 2026 reserve (no re-pricing). "
        "Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve.",
        "reserve_date": "2026-06-30",
        "loans": len(ids),
        "scenarios": {},
    }
    arr = {s: {k: d[k].to_numpy(dtype=float) for k in cols[2:]} for s, d in f.items()}
    for s, a in arr.items():
        out["scenarios"][s] = components(
            bal, a["lifetime_pd"], a["ead_at_default"], a["total"], a["pd_36"], a["tail"]
        )
    # weighted (60/30/10): each loan's PD, expected exposure and loss are the weighted sums across scenarios
    w = {k: sum(WEIGHTS[s] * arr[s][k] for s in WEIGHTS) for k in cols[2:]}
    out["scenarios"]["weighted"] = components(
        bal, w["lifetime_pd"], w["ead_at_default"], w["total"], w["pd_36"], w["tail"]
    )
    # tie back to the booked reserve file
    booked = json.loads(SRC.read_text(encoding="utf-8"))["dates"][R]
    ties = {s: booked["scenarios"][s]["reserve"] for s in WEIGHTS} | {"weighted": booked["weighted_reserve"]}
    out["ties_to_reserve_file"] = {s: abs(out["scenarios"][s]["reserve"] - v) < 0.01 for s, v in ties.items()}
    if not all(out["ties_to_reserve_file"].values()):
        raise SystemExit(f"does not tie to {SRC}: {out['ties_to_reserve_file']}")
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    for s, c in out["scenarios"].items():
        bw, ch = c["balance_weighted"], c["reconciling_chain"]
        print(f"{s:17s} reserve {c['reserve'] / 1e6:8.2f}m | bw PD {bw['lifetime_pd']:.4%} LGD {bw['lgd']:.4%} "
              f"EAD {bw['ead_ratio']:.4f} -> {c['simple_product_x_balance'] / 1e6:8.2f}m | chain PD {ch['lifetime_pd']:.4%} "
              f"EAD {ch['ead_ratio']:.4f} LGD {ch['lgd']:.4%} -> diff {c['chain_difference']:.4f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
