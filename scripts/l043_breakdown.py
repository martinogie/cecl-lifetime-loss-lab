"""L-043: where the Phase 5 mortgage-rate shocks move defaults and losses (no new projection).

    CECL_ENV=local python -m scripts.l043_breakdown [--write]

Re-prices the Phase 5 projections already made for the 30 June 2026 book: the Fed baseline and the
baseline with the mortgage rate +2 and -2 points. Each loan's lifetime chance of default and expected loss
are summed by loan group: current loan-to-value band, vintage group, and the note rate against the market
mortgage rate at June 2026 (the refinancing incentive). Rate-shock books not on the PC are downloaded and
removed again after pricing (the volume keeps them). Output (aggregates only): docs/phase6/l043_breakdown.json.
Rule (docs/phase6/test_designs.md): information only; L-043 is closed if the breakdown explains the
direction, otherwise it stays open for v3.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from cecl import engine, environment
from scripts import build_reserve as br
from scripts import build_stress as bs

OUT = Path("docs/phase6/l043_breakdown.json")
RUNS = {"baseline": "", "rates_up2": "rates_up2", "rates_down2": "rates_down2"}


def ltv_band(x: np.ndarray) -> np.ndarray:
    return np.select([x <= 60, x <= 80, x <= 90], ["<=60", "60-80", "80-90"], ">90")


def gap_band(gap: np.ndarray) -> np.ndarray:
    """Note rate minus the June 2026 market rate, in points: below market (locked in) to above (refinanceable)."""
    return np.select(
        [gap <= -2, gap <= -0.5, gap < 0.5, gap < 1.5],
        ["<= -2", "-2 to -0.5", "-0.5 to 0.5", "0.5 to 1.5"],
        ">= 1.5",
    )


def price_variant(variant: str, inp: dict[str, Any]) -> Any:
    tag = bs.stress.tag(bs.R, bs.R, "baseline", variant)
    path, downloaded = bs.fetch(tag)
    try:
        import pandas as pd

        book = pd.read_parquet(path)
        p_def = book[[f"pd_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
        p_pre = book[[f"pp_{k}" for k in range(1, engine.MONTHLY + 1)]].to_numpy(dtype=float)
        weights = book.groupby("property_state")["upb_start"].sum().to_dict()
        sc = bs.scenario_for(variant, "baseline", inp, weights)
        out = engine.price(
            book, p_def, p_pre, sc.macro, bs.R, inp["lgd"], inp["curtail"], sc.long_run["hpi_growth"]
        )
        frame = book[["loan_id", "upb_start", "current_ltv", "vintage_year", "rate_start"]].copy()
        frame["el"], frame["pd"], frame["payoffs_36"] = out["total"], out["lifetime_pd"], p_pre.sum(axis=1)
        frame["ead"] = out["ead_at_default"]
        return frame
    finally:
        if downloaded:
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    environment.current_env()
    import duckdb

    inp = br.load_inputs(duckdb.connect())
    frames = {name: price_variant(v, inp) for name, v in RUNS.items()}
    base = frames["baseline"]
    for name, f in frames.items():  # same loans, same order
        if not np.array_equal(f["loan_id"].to_numpy(), base["loan_id"].to_numpy()):
            raise SystemExit(f"loan order differs in {name}")
    market = float(inp["national"].assign(ym=lambda d: d["month"].astype(str).str[:7].str.replace("-", ""))
                   .query("variable == 'mortgage_rate_30y' and ym == @bs.R")["value"].iloc[0])  # fmt: skip
    keys = {
        "current_ltv_band": ltv_band(base["current_ltv"].to_numpy(dtype=float)),
        "vintage_group": br.group_of(base["vintage_year"]),
        "note_rate_vs_market": gap_band(base["rate_start"].to_numpy(dtype=float) - market),
    }
    bal = base["upb_start"].to_numpy(dtype=float)
    res: dict[str, Any] = {
        "note": "Aggregates only. Phase 5 rate-shock runs re-priced by loan group (L-043). Educational model; not an audited reserve.",
        "market_rate_june_2026": market,
        "totals": {
            n: {
                "reserve": float(f["el"].sum()),
                "lifetime_pd": float((f["pd"] * bal).sum() / bal.sum()),
                "expected_payoffs_36": float(f["payoffs_36"].sum()),
                "ead_at_default": float(f["ead"].sum()),
            }
            for n, f in frames.items()
        },  # fmt: skip
        "groups": {},
    }
    for key, g in keys.items():
        rows = {}
        for v in sorted(set(g)):
            m = g == v
            rows[str(v)] = {"balance": float(bal[m].sum()), "loans": int(m.sum())} | {
                n: {"reserve": float(f["el"].to_numpy()[m].sum()),
                    "lifetime_pd": float((f["pd"].to_numpy()[m] * bal[m]).sum() / bal[m].sum()),
                    "loss_per_default_dollar": float(f["el"].to_numpy()[m].sum() / max(f["ead"].to_numpy()[m].sum(), 1e-9))}
                for n, f in frames.items()}  # fmt: skip
        res["groups"][key] = rows
    print(json.dumps(res["totals"], indent=1))
    if args.write:
        OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
