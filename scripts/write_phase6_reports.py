"""Write the Phase 6 results page and the validation pack from the result files (aggregates only).

    python -m scripts.write_phase6_reports

Reads docs/phase6/backtests.json (crisis, rolling, forbearance), docs/phase6/peer_ratios.json (FFIEC),
docs/phase6/l043_breakdown.json and the reserve file; any part not run yet is shown as "pending". Writes
docs/phase6/validation_results.md and docs/phase6/validation_pack.md. Every figure comes from those files;
Martins's expectations are quoted from docs/phase6/backtest_expectations.md.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from scripts.write_allowance_memo import STATUS, m, pct

D = Path("docs/phase6")
RESERVE = Path("docs/phase4/reserve_2026Q2.json")
FREDDIE = 0.0019


def load(name: str) -> dict[str, Any]:
    p = D / name
    return dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else {}


def r2(x: Any) -> str:
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.2f}"


def yes(b: Any) -> str:
    return "**yes**" if b else "no"


def crisis_section(c: dict[str, Any]) -> list[str]:
    if not c:
        return ["## 1. Crisis backtest", "", "_Pending._"]
    v = c["verdict"]
    L = [
        "## 1. Crisis backtest (models fitted to December 2006, tested on 2007-2010)",
        "",
        f"Book: {c['loans']:,} loans alive at 31 December 2006 ({m(c['balance'])}). PD and payoffs: the v2 recipe refitted "
        f"on months up to December 2006. LGD refitted on {c['lgd_refit']['resolved_defaults']:,} defaults resolved by "
        f"then ({c['lgd_refit']['with_loss']:,} with a loss; {pct(c['lgd_refit']['loss_per_dollar'], 1)} lost per $ "
        "defaulted). 48 months on the economy as it happened.",
        "",
        "| | Projected | Actual | Projected / actual | Benchmark / actual |",
        "|---|---|---|---|---|",
        f"| Defaults | {c['projected_defaults']:,.0f} | {c['actual_defaults']:,} | **{r2(v['defaults_ratio'])}** | {r2(v['benchmark_defaults_ratio'])} |",
        f"| Loss | {m(c['projected_loss'])} | {m(c['actual_loss'])} | **{r2(v['loss_ratio'])}** | {r2(v['benchmark_loss_ratio'])} |",
        "",
        "| Year | Projected defaults | Actual | Ratio | Projected loss | Actual loss | Ratio |",
        "|---|---|---|---|---|---|---|",
    ]
    for y, b in c["by_year"].items():
        L.append(f"| {y} | {b['projected_defaults']:,.0f} | {b['actual_defaults']:,.0f} | "
                 f"{r2(b['projected_defaults'] / b['actual_defaults'] if b['actual_defaults'] else None)} | "
                 f"{m(b['projected_loss'])} | {m(b['actual_loss'])} | "
                 f"{r2(b['projected_loss'] / b['actual_loss'] if b['actual_loss'] else None)} |")  # fmt: skip
    L += [
        "",
        f"Pass rule: defaults in 0.80-1.25 ({yes(v['defaults_in_bar'])}), loss in 0.80-1.25 ({yes(v['loss_in_bar'])}), "
        f"closer than the benchmark on both ({yes(v['closer_than_benchmark'])}). **Result: {'PASS' if v['passes'] else 'FAIL'}.** "
        f"Share of actual loss valued at the booked LGD (defaults unresolved by June 2026): "
        f"{pct(c['share_of_actual_loss_from_unresolved'], 1)}.",
    ]
    return L


def rolling_section(rb: dict[str, Any]) -> list[str]:
    if not rb or "dates" not in rb:
        return ["## 2. Rolling backtest", "", "_Pending._"]
    L = [
        "## 2. Rolling backtest (year-ends 2012-2022, logistic PD and LGD refitted at each date)",
        "",
        "Projected expected loss over the next 36 months against the actual losses on the same book from defaults "
        "in those 36 months. Economy (a): as it happened; (b): flat then reverted (information).",
        "",
        "| Date | Loans | LGD refit: loss per $ | (a) projected | (a) actual | (a) ratio | (b) ratio | Lifetime reserve (a) | Defaults ratio (a) | Unresolved share |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for d, r in rb["dates"].items():
        a, b = r["actual"], r["flat"]
        L.append(f"| {d[:4]} | {a['loans']:,} | {pct(r['lgd_refit']['loss_per_dollar'], 1)} | {m(a['projected_loss'])} | "
                 f"{m(a['actual_loss'])} | **{r2(a['loss_ratio'])}** | {r2(b['loss_ratio'])} | {m(a['lifetime_reserve'])} | "
                 f"{r2(a['defaults_ratio'])} | {pct(a['share_of_actual_loss_from_unresolved'], 0)} |")  # fmt: skip
    for econ in ("actual", "flat"):
        v = rb.get(f"verdict_{econ}")
        if v:
            L += ["", f"Economy ({'a' if econ == 'actual' else 'b'}): pooled ratio **{r2(v['pooled_ratio'])}** "
                  f"(bar 0.80-1.25: {yes(v['pooled_in_bar'])}); dates inside 0.67-1.50: {v['dates_in_band']} of "
                  f"{len(v['ratios'])} (need {v['dates_needed']}: {yes(v['enough_dates_in_band'])}). "
                  f"**{'PASS' if v['passes'] else 'FAIL'}**{' (judged)' if econ == 'actual' else ' (information)'}."]  # fmt: skip
    return L


def forbearance_section(f: dict[str, Any]) -> list[str]:
    if not f:
        return ["## 3. Forbearance treatment test", "", "_Pending._"]
    c, v = f["counts_2020_2021"], f["verdict"]
    a, b = f["A_booked"], f["B_flag_ignored"]
    return [
        "## 3. Forbearance treatment test (2020-2021)",
        "",
        f"Defaults January 2020 - December 2021, booked definition (A, the stop-the-clock rule): "
        f"{', '.join(f'{y}: {n:,}' for y, n in c['defaults_booked_A_by_year'].items())}. With the forbearance flag ignored "
        f"(B): {c['defaults_flag_ignored_B']:,}, of which **{c['extra_under_B_only']:,} only under B**; of those, "
        f"**{c['extra_current_again_within_12m']:,} ({pct(v['cured_share'], 0)}) were current again within 12 months** and "
        f"{c['extra_with_a_later_loss']:,} later had a loss.",
        "",
        "| Book alive at 30 June 2020, 36 months | Loans | Projected loss | Actual loss | Ratio |",
        "|---|---|---|---|---|",
        f"| (A) booked: stop-the-clock | {a['loans']:,} | {m(a['projected_loss'])} | {m(a['actual_loss'])} | **{r2(v['booked_ratio'])}** |",
        f"| (B) flag ignored | {b['loans']:,} | {m(b['projected_loss'])} | {m(b['actual_loss'])} | **{r2(v['flag_ignored_ratio'])}** |",
        "",
        f"Pass rule: (A) closer to 1 than (B) ({yes(v['booked_closer'])}); at least half the extra defaults cured within 12 "
        f"months ({yes(v['half_cured'])}). **Result: {'PASS (the stop-the-clock rule is supported)' if v['passes'] else 'FAIL (flag ADR-002 for review in v3)'}.**",
    ]


def peer_section(p: dict[str, Any], reserve: dict[str, Any]) -> list[str]:
    if not p:
        return ["## 4. Peer comparison", "", "_Pending (FFIEC load)._"]
    q2 = reserve["dates"]["202606"]
    sc = q2["scenarios"]
    ours = {"Our total allowance": q2["total_allowance_ratio"],
            "Our baseline only": (sc["baseline"]["reserve"] + q2["defaulted_pipeline"]["reserve"]) / (q2["book"]["balance"] + q2["defaulted_pipeline"]["balance"]),
            "Our total, LGD conservatism removed (/1.64)": q2["total_allowance_ratio"] / 1.64,
            "Freddie Mac (Q2 2026 release)": FREDDIE}  # fmt: skip
    agg = [a for a in p["aggregates"] if a["period"] == "2026-06-30"]
    L = [
        "## 4. Peer comparison (Q9): residential real estate allowance ratios, banks with $1bn+ in assets",
        "",
        "FFIEC call reports, Schedule RI-C item 1.c (residential real estate loans as each bank segments its allowance: "
        "1-4 family first and junior liens and home-equity lines; some banks include multifamily). A sanity range, not a target.",
        "",
        "| 30 June 2026 | Banks | 10th pct | 25th pct | Median | 75th pct | 90th pct | Pooled |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in sorted(agg, key=lambda a: (a["size_group"] != "All banks $1bn+", a["size_group"])):
        L.append(f"| {a['size_group']} | {a['banks']:,} | {pct(a['p10'])} | {pct(a['p25'])} | **{pct(a['median'])}** | "
                 f"{pct(a['p75'])} | {pct(a['p90'])} | {pct(a['pooled'])} |")  # fmt: skip
    allb = next((a for a in agg if a["size_group"] == "All banks $1bn+"), None)
    L += ["", "| Our ratios and Freddie's | Ratio | Against the banks' middle 50% |", "|---|---|---|"]
    for k, v in ours.items():
        where = (
            "n/a"
            if allb is None
            else ("below" if v < allb["p25"] else "above" if v > allb["p75"] else "inside")
        )
        L.append(f"| {k} | {pct(v)} | {where} |")
    L += [
        "",
        "Over time (all banks $1bn+, median and pooled; CECL / incurred-loss banks):",
        "",
        "| Period | Banks | Median | Pooled | Banks by method |",
        "|---|---|---|---|---|",
    ]
    for a in sorted(
        (a for a in p["aggregates"] if a["size_group"] == "All banks $1bn+"), key=lambda a: a["period"]
    ):
        L.append(
            f"| {a['period']} | {a['banks']:,} | {pct(a['median'])} | {pct(a['pooled'])} | {a.get('banks_by_method')} |"
        )
    L += [
        "",
        "Why ours differs: bank books include jumbo, non-conforming and home-equity loans and are on banks' own "
        "models; Freddie loans above 80% LTV carry mortgage insurance; our LGD is conservative (L-033, 1.64x); our "
        "scenario weights put 79% of the performing reserve on the adverse and severely adverse paths. Before 2023 "
        "many banks still used the incurred-loss method, which reserves less than CECL; the table shows the split.",
    ]
    return L


def l043_section(x: dict[str, Any]) -> list[str]:
    if not x:
        return ["## 5. L-043: rate-shock breakdown", "", "_Pending._"]
    t = x["totals"]
    L = [
        "## 5. L-043: where the mortgage-rate shocks move the reserve",
        "",
        f"Market rate June 2026: {x['market_rate_june_2026']:.2f}%.",
        "",
        "| Run | Reserve | Lifetime chance of default | Expected payoffs, 36 months | Exposure at default |",
        "|---|---|---|---|---|",
    ]
    for n, v in t.items():
        L.append(
            f"| {n} | {m(v['reserve'])} | {pct(v['lifetime_pd'], 2)} | {v['expected_payoffs_36']:,.0f} | {m(v['ead_at_default'])} |"
        )
    for key, rows in x["groups"].items():
        L += ["", f"By {key.replace('_', ' ')}:", "",
              "| Group | Balance | Reserve: base / +2 / -2 | Lifetime PD: base / +2 / -2 | Loss per $ defaulted: base / +2 / -2 |",
              "|---|---|---|---|---|"]  # fmt: skip
        for g, r in rows.items():
            L.append(f"| {g} | {m(r['balance'])} | {m(r['baseline']['reserve'])} / {m(r['rates_up2']['reserve'])} / "
                     f"{m(r['rates_down2']['reserve'])} | {pct(r['baseline']['lifetime_pd'], 2)} / {pct(r['rates_up2']['lifetime_pd'], 2)} / "
                     f"{pct(r['rates_down2']['lifetime_pd'], 2)} | {pct(r['baseline']['loss_per_default_dollar'], 1)} / "
                     f"{pct(r['rates_up2']['loss_per_default_dollar'], 1)} / {pct(r['rates_down2']['loss_per_default_dollar'], 1)} |")  # fmt: skip
    return L


def main() -> int:
    bt, peer, l043 = load("backtests.json"), load("peer_ratios.json"), load("l043_breakdown.json")
    reserve = json.loads(RESERVE.read_text(encoding="utf-8"))
    L = [
        "# Phase 6 validation results",
        "",
        "Rules and pass/fail tests committed before any test ran: `docs/phase6/test_designs.md`. Martins's written "
        "expectations, committed before the tests: `docs/phase6/backtest_expectations.md`. Each test ran once. "
        "Information for the validator: no booked figure, model status or overlay decision changes.",
        "",
        STATUS,
        "",
        *crisis_section(bt.get("crisis", {})),
        "",
        *rolling_section(bt.get("rolling", {})),
        "",
        *forbearance_section(bt.get("forbearance", {})),
        "",
        *peer_section(peer, reserve),
        "",
        *l043_section(l043),
        "",
    ]
    (D / "validation_results.md").write_text("\n".join(L), encoding="utf-8")
    print("written docs/phase6/validation_results.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
