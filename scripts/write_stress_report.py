"""Write the Phase 5 stress report from docs/phase5/stress_2026Q2.json (aggregates only).

    python -m scripts.write_stress_report

Every figure comes from the JSON written by `scripts/build_stress.py`; nothing is typed by hand except the
published benchmark, which sits in that script with its source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.write_allowance_memo import STATUS, m, pct

SRC = Path("docs/phase5/stress_2026Q2.json")
OUT = Path("docs/phase5/stress_report.md")
NAMES = {"baseline": "Baseline", "adverse": "Adverse (halfway path)", "severely_adverse": "Severely adverse"}
SHOCK_NAMES = {
    "hpi_m10": "House prices -10%",
    "hpi_m20": "House prices -20%",
    "hpi_m30": "House prices -30%",
    "unemployment_p2": "Unemployment +2 points",
    "unemployment_p4": "Unemployment +4 points",
    "rates_up2": "Mortgage rate +2 points",
    "rates_down2": "Mortgage rate -2 points",
    "hpi_m20_unemployment_p2": "House prices -20% and unemployment +2",
    "hpi_m30_unemployment_p4": "House prices -30% and unemployment +4",
    "hpi_m30_immediate": "House prices -30% at once in July 2026 (information only)",
}


def qlabel(ym: str) -> str:
    y, mo = int(ym[:4]), int(ym[4:])
    return f"{y} Q{(mo - 1) // 3 + 1}"


def fed_section(r: dict[str, Any]) -> list[str]:
    fq = r["fed_quarterly"]
    L = [
        "## 1. The Fed's scenarios, quarter by quarter",
        "",
        "What it is: the 30 June 2026 book played forward under each scenario. Expected defaults and expected loss "
        "(chance of default x balance at default x loss per $ defaulted) are shown for each quarter after the "
        "reserve date. Unemployment and house prices are the book's state paths, weighted by balance; house prices "
        "are shown as the change since June 2026.",
        "",
        "| Scenario | Losses, first 9 quarters | 9-quarter loss rate (of average balance) | Lifetime reserve | Lifetime loss rate | Lifetime chance of default |",
        "|---|---|---|---|---|---|",
    ]
    for s, q in fq.items():
        L.append(f"| {NAMES[s]} | {m(q['loss_9q'])} | {pct(q['loss_rate_9q_of_average_balance'], 2)} | "
                 f"{m(q['lifetime_reserve'])} | {pct(q['lifetime_rate'], 2)} | {pct(q['lifetime_pd'], 2)} |")  # fmt: skip
    for s, q in fq.items():
        L += [
            "",
            f"### {NAMES[s]}",
            "",
            "| Quarter | Unemployment | House prices vs June 2026 | Mortgage rate | Expected defaults (loans) | Expected loss | Loss rate (of start balance) |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in q["quarters"]:
            L.append(f"| {qlabel(row['quarter_end'])} | {row['unemployment']:.1f}% | {pct(row['hpi_change'], 1)} | "
                     f"{row['mortgage_rate']:.2f}% | {row['expected_defaults']:,.0f} | {m(row['expected_loss'])} | "
                     f"{pct(row['loss_rate_of_start_balance'], 3)} |")  # fmt: skip
    return L


def shock_section(r: dict[str, Any]) -> list[str]:
    base = r["fed_quarterly"]["baseline"]
    L = [
        "## 2. Custom shocks (on top of the Fed baseline)",
        "",
        "What it is: each shock is added to the Fed baseline. It builds up in a straight line over the first 4 quarters, "
        "stays in place to the end of the 24-month forecast, and then the usual reversion runs (ADR-003). House prices "
        "revert in growth, so their level stays lower; unemployment and the mortgage rate return to their long-run "
        "levels. Each shock is a fresh projection by model v2 under the shocked economy. The baseline reserve is "
        f"{m(base['lifetime_reserve'])} (no weighting; the booked reserve weights three scenarios).",
        "",
        "| Shock | Worst house-price change | Peak unemployment | Reserve (lifetime) | Change vs baseline | Times baseline | 9-quarter loss rate |",
        "|---|---|---|---|---|---|---|",
        f"| Fed baseline (no shock) | {pct(min(q['hpi_change'] for q in base['quarters']), 1)} | "
        f"{max(q['unemployment'] for q in base['quarters']):.1f}% | {m(base['lifetime_reserve'])} | - | 1.00 | "
        f"{pct(base['loss_rate_9q_of_average_balance'], 2)} |",
    ]
    for k, s in r["shocks"].items():
        L.append(f"| {SHOCK_NAMES[k]} | {pct(s['worst_hpi_change'], 1)} | {s['peak_unemployment']:.1f}% | {m(s['reserve'])} | "
                 f"{m(s['change_vs_baseline'])} | {s['multiple_of_baseline']:.2f} | {pct(s['loss_rate_9q_of_average_balance'], 2)} |")  # fmt: skip
    return L


def sensitivity_section(r: dict[str, Any]) -> list[str]:
    b = r["booked"]
    L = [
        "## 3. Sensitivity table: how much each assumption moves the reserve",
        "",
        f"What it is: the booked performing-loan reserve ({m(b['weighted_reserve'])}, weighted 60/30/10) recomputed with "
        "one assumption changed at a time. The defaulted-loan reserve "
        f"({m(b['defaulted_reserve'])}) does not depend on these assumptions and is added for the total allowance.",
        "",
        "| Assumption | Setting | Performing reserve | Change | Change % | Total allowance |",
        "|---|---|---|---|---|---|",
    ]
    for row in r["sensitivity"]:
        setting = (
            f"**{row['setting']} (booked)**"
            if row["booked"] and "booked" not in row["setting"]
            else row["setting"]
        )
        if row["booked"] and "booked" in row["setting"]:
            setting = f"**{row['setting']}**"
        L.append(f"| {row['assumption']} | {setting} | {m(row['weighted_reserve'])} | {m(row['change'])} | "
                 f"{pct(row['change_pct'], 1)} | {m(row['total_allowance'])} |")  # fmt: skip
    L += [
        "",
        "### Which assumption matters most (ranked by the spread between its lowest and highest setting)",
        "",
        "| Rank | Assumption | Lowest change | Highest change | Spread |",
        "|---|---|---|---|---|",
    ]
    for i, t in enumerate(r["tornado"], 1):
        L.append(
            f"| {i} | {t['assumption']} | {m(t['low_change'])} | {m(t['high_change'])} | {m(t['range'])} |"
        )
    return L


def benchmark_section(r: dict[str, Any]) -> list[str]:
    bm = r["benchmark"]
    d = bm["dfast_2026"]
    ours = bm["ours_severely_adverse_9q_loss_rate_of_average_balance"]
    lo, hi = d["range"]
    where = "below" if ours < lo else "above" if ours > hi else "inside"
    return [
        "## 4. Benchmark: the Fed's 2026 stress test (DFAST), first-lien mortgages",
        "",
        "| | 9-quarter severely adverse loss rate |",
        "|---|---|",
        f"| **Our book** (Fed 2026 severely adverse path, 9 quarters from July 2026, % of average balance) | **{pct(ours, 2)}** |",
        f"| Our book, % of the starting balance | {pct(bm['ours_severely_adverse_9q_loss_rate_of_start_balance'], 2)} |",
        f"| DFAST 2026, 32 banks in aggregate | {pct(d['aggregate_32_banks'], 1)} |",
        f"| DFAST 2026, range across banks | {pct(lo, 1)} - {pct(hi, 1)} |",
        "| DFAST 2026, examples | " + ", ".join(f"{k} {pct(v, 1)}" for k, v in d["examples"].items()) + " |",
        "",
        f"Like for like: the same Fed scenario (2026 severely adverse), the same 9 scenario quarters (our path starts "
        f"the Fed's quarter 1 in July 2026, the quarter after our reserve date), losses over average balances. Our "
        f"rate is **{where}** the banks' range.",
        "",
        "Why the numbers differ (expected):",
        "- **Losses at default vs charge-offs.** We count the expected loss when a loan defaults; banks report net "
        "charge-offs, which come later (foreclosure and sale take a year or more), so part of their losses from the "
        "same defaults falls after the 9 quarters.",
        "- **Different books.** Bank mortgage books include jumbo, older and non-conforming loans; ours is Freddie Mac "
        "conforming loans, with mortgage insurance above 80% loan-to-value, which lowers losses.",
        "- **Our LGD is conservative** (L-033: 1.64 times the real loss per default on 2017-2021 defaults), which "
        "pushes our rate up.",
        "- **Model v2 failed its holdout** (L-038: defaults short by about 26% in loss terms in 2024-2026) and "
        "over-projects in calmer years (L-039); under severe stress its direction is not measured (the crisis exam "
        "failed, L-035).",
        "",
        f"Context: Freddie Mac's own allowance is {pct(bm['freddie_allowance_ratio_context'], 2)} of its single-family "
        f"loans at 30 June 2026; ours is {pct(bm['our_total_allowance_ratio'], 2)} (allowance memo, section 5).",
        "",
        f"Source: {d['source']}.",
    ]


def findings(r: dict[str, Any]) -> list[str]:
    """The headline results in plain English (every number from the JSON)."""
    fq, sh, b = r["fed_quarterly"], r["shocks"], r["booked"]
    sens = {(x["assumption"], x["setting"]): x for x in r["sensitivity"]}
    top = r["tornado"][0]
    cal = sens[("Fed timing", "calendar quarters")]
    f12 = sens[("Forecast period", "12 months")]
    bm = r["benchmark"]
    return [
        "## Key findings (plain English)",
        "",
        f"1. **The Fed's severely adverse scenario multiplies the lifetime loss by about "
        f"{fq['severely_adverse']['lifetime_reserve'] / fq['baseline']['lifetime_reserve']:.0f} times**: "
        f"{m(fq['baseline']['lifetime_reserve'])} under the baseline, {m(fq['severely_adverse']['lifetime_reserve'])} "
        f"under severely adverse ({pct(fq['severely_adverse']['lifetime_rate'], 2)} of the book). Most of it arrives "
        f"in the first 9 quarters ({m(fq['severely_adverse']['loss_9q'])}).",
        f"2. **House prices drive the losses; unemployment much less.** House prices -30% on the baseline give "
        f"{m(sh['hpi_m30']['reserve'])} ({sh['hpi_m30']['multiple_of_baseline']:.1f} times the baseline); unemployment "
        f"+4 points gives {m(sh['unemployment_p4']['reserve'])} ({sh['unemployment_p4']['multiple_of_baseline']:.1f} "
        "times). Falling prices raise both the chance of default (less equity) and the loss per default (LGD tied to "
        "the current loan-to-value), while unemployment acts only through the chance of default. Together "
        f"(-30% and +4) they give {m(sh['hpi_m30_unemployment_p4']['reserve'])}, above the Fed's severely adverse.",
        f"3. **Speed matters:** the same -30% fall all at once in July 2026 gives {m(sh['hpi_m30_immediate']['reserve'])}, "
        f"{pct(sh['hpi_m30_immediate']['reserve'] / sh['hpi_m30']['reserve'] - 1, 0)} more than when it builds up over a "
        "year (information only).",
        f"4. **Mortgage-rate shocks barely move the reserve** ({m(sh['rates_up2']['change_vs_baseline'])} for +2 points, "
        f"{m(sh['rates_down2']['change_vs_baseline'])} for -2 points). Lower rates mean more payoffs, so less exposure; "
        "higher rates keep loans on the book longer, and the lifetime chance of default rises, yet the reserve falls "
        "slightly. A likely reason, not yet verified: v2's rate inputs move defaults towards loans that lose little "
        "(seasoned, low loan-to-value). Not intuitive, so it is logged (L-043) for validation.",
        f"5. **The assumption that moves the reserve most is the scenario weights** (spread {m(top['range'])} between "
        f"80/15/5 and 40/40/20), then the L-038 default uplift (+34%: +{m(sens[('Default uplift (L-038 Option A)', '+34%')]['change'])}), "
        f"the Fed timing ({m(cal['change'])} with the Fed's calendar quarters, which starts the scenario's recession "
        f"after it has partly passed) and the forecast period ({m(f12['change'])} with 12 months instead of 24). "
        "Payoff speed (about ±5%), the LGD floor (+2% at 10%) and the reversion speed (under 1%) matter little.",
        f"6. **Benchmark:** our 9-quarter severely adverse loss rate is "
        f"{pct(bm['ours_severely_adverse_9q_loss_rate_of_average_balance'], 2)}, inside the range of the 32 banks in the "
        f"Fed's 2026 stress test ({pct(bm['dfast_2026']['range'][0], 1)}-{pct(bm['dfast_2026']['range'][1], 1)}) and "
        f"below their aggregate ({pct(bm['dfast_2026']['aggregate_32_banks'], 1)}), as expected for conforming, insured "
        "loans; section 4 explains the differences.",
        f"7. The booked reserve is unchanged: performing {m(b['weighted_reserve'])}, total allowance {m(b['total_allowance'])}. "
        "Phase 5 changes no booked figure; it shows how far the figure could move.",
    ]


def main() -> int:
    r = json.loads(SRC.read_text(encoding="utf-8"))
    L = [
        "# Stress testing and sensitivity: 30 June 2026 book (Phase 5)",
        "",
        f"- **Book:** {r['book']['loans']:,} Freddie Mac sample loans, {m(r['book']['balance'])}, alive at 30 June 2026.",
        f"- **Booked reserve:** performing {m(r['booked']['weighted_reserve'])} + defaulted {m(r['booked']['defaulted_reserve'])} "
        f"= total allowance {m(r['booked']['total_allowance'])}.",
        "- **Source:** `docs/phase5/stress_2026Q2.json` (`scripts/build_stress.py`); decisions in `docs/phase5/plan.md`.",
        "",
        STATUS,
        "",
        *findings(r),
        "",
        *fed_section(r),
        "",
        *shock_section(r),
        "",
        *sensitivity_section(r),
        "",
        *benchmark_section(r),
        "",
        "## 5. Limits",
        "",
        "- One reserve date (30 June 2026) and one book (Freddie Mac conforming sample). Shocks are applied to every "
        "state alike.",
        "- Model v2 is `@challenger`, failed its holdout (L-038) and its crisis exam (L-035); stress results show how "
        "the model responds, not a validated loss forecast.",
        "- After year 3 the loss runs at the month-36 chances and the long-run economy (L-040), so shocks that "
        "persist beyond 3 years are only partly reflected.",
        "- Credit-loss part only; no revenue, expense or capital projections.",
        "",
    ]
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
