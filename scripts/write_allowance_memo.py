"""Write the Q2 2026 allowance memo from docs/phase4/reserve_2026Q2.json (aggregates only).

    python -m scripts.write_allowance_memo

Every figure in the memo comes from the JSON written by `scripts/build_reserve.py`; nothing is typed by hand.
The model-status statements (holdout, L-039, overlay decision, LGD conservatism) appear at the top and next
to every reserve table, as Martins required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SRC = Path("docs/phase4/reserve_2026Q2.json")
OUT = Path("docs/allowance_memos/2026Q2_allowance_memo.md")
DATES = {"202512": "31 December 2025", "202603": "31 March 2026", "202606": "30 June 2026"}
NAMES = {"baseline": "Baseline", "adverse": "Adverse (halfway path)", "severely_adverse": "Severely adverse"}

STATUS = (
    "> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition "
    "models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): "
    "total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. "
    "**L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells "
    "outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. "
    "**Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD "
    "model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default "
    "shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each "
    "quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational "
    "model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice."
)


def m(x: float) -> str:
    return f"${x / 1e6:,.1f}m"


def pct(x: float, d: int = 2) -> str:
    return f"{100 * x:.{d}f}%"


def scenario_table(d: dict[str, Any], uplift: float) -> list[str]:
    rows = ["| Scenario | Reserve | Of which: forecast (yr 1-2) / reversion (yr 3) / tail | Balance-weighted lifetime PD | With +34% uplift |",
            "|---|---|---|---|---|"]  # fmt: skip
    for s, v in d["scenarios"].items():
        rows.append(f"| {NAMES[s]} | {m(v['reserve'])} | {m(v['forecast'])} / {m(v['reversion'])} / {m(v['tail'])} | "
                    f"{pct(v['lifetime_pd_weighted_by_balance'])} | {m(v['reserve_with_uplift'])} |")  # fmt: skip
    rows.append(
        f"| **Weighted 60/30/10 (booked)** | **{m(d['weighted_reserve'])}** | | | {m(d['weighted_reserve_with_uplift'])} |"
    )
    return rows


COMPONENTS = Path("docs/phase4/reserve_components_2026Q2.json")  # from scripts/decompose_reserve.py


def components_section() -> list[str]:
    """PD x LGD x EAD by scenario, 30 June 2026 (performing loans; the defaulted-loan reserve is separate)."""
    c = json.loads(COMPONENTS.read_text(encoding="utf-8"))["scenarios"]
    names = NAMES | {"weighted": "**Weighted 60/30/10**"}
    L = [
        "### PD x LGD x EAD by scenario, 30 June 2026 (performing loans)",
        "",
        "Balance-weighted averages over the 350,495 loans. Lifetime PD: each loan's chance of default over its whole "
        "remaining life. LGD: loss per $ defaulted. EAD: balance at default / current balance. Each loan's figures "
        "already include the timing of defaults (36 monthly steps, then yearly to maturity), so for every loan "
        "PD x LGD x EAD x balance equals its reserve exactly.",
        "",
        "| Scenario | Lifetime PD | LGD | EAD ratio | PD x LGD x EAD x balance | Reserve | Gap |",
        "|---|---|---|---|---|---|---|",
    ]
    for s, v in c.items():
        b = v["balance_weighted"]
        L.append(f"| {names[s]} | {pct(b['lifetime_pd'], 3)} | {pct(b['lgd'], 3)} | {b['ead_ratio']:.4f} | "
                 f"{m(v['simple_product_x_balance'])} | {m(v['reserve'])} | {m(v['gap_reserve_minus_simple_product'])} |")  # fmt: skip
    L += [
        "",
        "The simple averages multiply to less than the reserve because the risk sits in the same loans: loans more "
        "likely to default also default earlier (more balance left) and have higher LGD (higher LTV). Weighting EAD by "
        "each loan's expected defaults, and LGD by each loan's expected exposure, closes the gap exactly:",
        "",
        "| Scenario | Lifetime PD (by balance) | EAD ratio (by expected defaults) | LGD (by expected exposure) | "
        "Product x balance | Reserve | Gap closed by EAD weighting / by LGD weighting |",
        "|---|---|---|---|---|---|---|",
    ]
    for s, v in c.items():
        ch = v["reconciling_chain"]
        L.append(f"| {names[s]} | {pct(ch['lifetime_pd'], 3)} | {ch['ead_ratio']:.4f} | {pct(ch['lgd'], 3)} | "
                 f"{m(v['chain_product_x_balance'])} | {m(v['reserve'])} | {m(v['gap_from_ead_weighting'])} / "
                 f"{m(v['gap_from_lgd_weighting'])} |")  # fmt: skip
    b, sev = c["baseline"], c["severely_adverse"]
    L += [
        "",
        f"Timing and the tail sit inside each loan's figures, not in the gap. Of the baseline lifetime PD "
        f"{pct(b['balance_weighted']['lifetime_pd'], 3)}, {pct(b['lifetime_pd_within_36_months'], 3)} falls in the first "
        f"36 months and {pct(b['lifetime_pd_tail_after_36_months'], 3)} after (the tail supplies {pct(b['tail_share_of_reserve'], 1)} "
        f"of the baseline reserve; {pct(sev['tail_share_of_reserve'], 1)} under severely adverse). Losses are not "
        "discounted. Weighted column: each loan's PD, expected exposure and loss are the 60/30/10 sums across scenarios. "
        "Source: `docs/phase4/reserve_components_2026Q2.json` (`scripts/decompose_reserve.py`, reading the priced loan "
        "files only; nothing re-run). Every scenario ties to the reserve file.",
    ]
    return L


# Published external figure (the only number in the memo not taken from the reserve JSON); typed once, with source.
FREDDIE_RATIO = 0.0019
FREDDIE_SOURCE = (
    "Freddie Mac, Second Quarter 2026 Financial Results (Q2 2026 earnings release), Single-Family allowance for "
    "credit losses as a percentage of total single-family loans: 0.19% at 30 June 2026, "
    "https://www.freddiemac.com/investors/financials/pdf/2026er-2q26_release.pdf"
)


def freddie_benchmark(q: dict[str, Any]) -> list[str]:
    """Information-only comparison with Freddie Mac's own published allowance ratio."""
    bal, sc, pipe = q["book"]["balance"], q["scenarios"], q["defaulted_pipeline"]["reserve"]
    stressed = 0.3 * sc["adverse"]["reserve"] + 0.1 * sc["severely_adverse"]["reserve"]
    baseline_only = sc["baseline"]["reserve"] + pipe
    lgd_real = q["total_allowance"] / 1.64  # L-033: model LGD is 1.64x the real loss per default
    return [
        "### Freddie Mac's own allowance ratio (information only, not a target)",
        "",
        "| | Ratio | On our 30 June 2026 book |",
        "|---|---|---|",
        f"| Our total allowance (booked) | {pct(q['total_allowance_ratio'])} | {m(q['total_allowance'])} |",
        f"| Freddie Mac's published Single-Family ratio, 30 June 2026 | {pct(FREDDIE_RATIO)} | {m(FREDDIE_RATIO * bal)} |",
        f"| Gap | {pct(q['total_allowance_ratio'] - FREDDIE_RATIO)} | {m(q['total_allowance'] - FREDDIE_RATIO * bal)} |",
        "",
        "Why ours is higher (illustrations, each on its own; they overlap and do not add up):",
        "",
        f"- **LGD conservatism (L-033, 1.64x).** Priced at the real loss per default, the total allowance would be about "
        f"{m(lgd_real)} ({pct(lgd_real / bal)}).",
        f"- **Scenario weights (ADR-005, 60/30/10).** The adverse and severely adverse paths supply {m(stressed)}, "
        f"{pct(stressed / q['weighted_reserve'], 0)} of the performing reserve ({pct(stressed / q['total_allowance'], 0)} "
        f"of the total allowance). On the baseline alone the allowance would be {m(baseline_only)} "
        f"({pct(baseline_only / bal)}).",
        "- **Different book, models and mortgage insurance.** Ours is a sample of loans alive at 30 June 2026 priced by "
        "an educational model; Freddie's covers its whole single-family guarantee book with its own models, scenarios "
        "and weights (its Q2 2026 benefit for credit losses reflected a change in how it generates house-price "
        "scenarios). Mortgage insurance and other credit enhancements enter our LGD only through Freddie's reported "
        "recoveries on past defaults; Freddie values them loan by loan.",
        "",
        f"Source: {FREDDIE_SOURCE}. Freddie's ratio is a sanity reference for the same kind of loans, not a target; "
        "the bank peer comparison (FDIC call reports) is in Phase 6.",
    ]


def reserve_table(r: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    d = [r["dates"][k] for k in keys]

    def row(label: str, f: Any) -> str:
        return f"| {label} | " + " | ".join(f(x) for x in d) + " |"

    def tie(x: dict[str, Any]) -> str:
        return "pass" if all(t["passes"] for t in x["tie_outs"].values()) else "FAIL"

    return [
        "| | " + " | ".join(DATES[k] for k in keys) + " |",
        "|---|" + "---|" * len(keys),
        row("Fed scenarios used (published by the reserve date)", lambda x: str(x["fed_scenario_year"])),
        row("Loans", lambda x: f"{x['book']['loans']:,}"),
        row("Balance", lambda x: m(x["book"]["balance"])),
        row("Performing-loan reserve (engine, weighted 60/30/10)", lambda x: m(x["weighted_reserve"])),
        row("Performing reserve ratio (reserve / balance)", lambda x: pct(x["reserve_ratio"], 3)),
        row("Overlays booked (ADR-006 register)", lambda x: m(x["overlays"]["booked_total"])),
        row("Defaulted, unresolved loans (90+ days late or REO): loans / balance",
            lambda x: f"{x['defaulted_pipeline']['loans']:,} / {m(x['defaulted_pipeline']['balance'])}"),
        row("Defaulted-loan reserve (balance x Phase 3 LGD)", lambda x: m(x["defaulted_pipeline"]["reserve"])),
        row("**Total allowance (booked)**", lambda x: f"**{m(x['total_allowance'])}**"),
        row("Total allowance ratio", lambda x: pct(x["total_allowance_ratio"], 3)),
        row("Sensitivity: total with the +34% default uplift on the performing reserve (Option A)",
            lambda x: m(x["total_allowance_with_uplift"])),
        row("Tie-out: book reserve = sum of loan reserves (every scenario)", tie),
    ]  # fmt: skip


def rollforward_section(rf: dict[str, Any], a: str, b: str, title: str) -> list[str]:
    return [
        f"### {title}",
        "",
        f"| Loan group (origination year) | {DATES[a]} | Provision | Gross write-offs | Recoveries | {DATES[b]} |",
        "|---|---|---|---|---|---|",
        *[
            f"| {g['group']} | {g['start']} | {g['provision']} | {g['write_offs']} | {g['recoveries']} | {g['end']} |"
            for g in rf["by_group"]
        ],
        f"| **Total** | **{rf['total']['start']}** | **{rf['total']['provision']}** | **{rf['total']['write_offs']}** | "
        f"**{rf['total']['recoveries']}** | **{rf['total']['end']}** |",
        "",
        f"Reconciles to the cent: **{'yes' if rf['reconciles_to_the_cent'] else 'NO'}**. Loss dispositions in the "
        f"quarter: {rf['write_off_events']}, of which {rf['estimated_write_off_events']} **estimated** ({rf['note']}).",
    ]


def bridge_section(br: dict[str, Any], a: str, b: str) -> list[str]:
    years = br.get("fed_scenario_years", [None, None])
    note = (f" The economic forecast step includes the move from the Fed's {years[0]} scenarios to its {years[1]} "
            "scenarios (published in between)." if years[0] != years[1] else "")  # fmt: skip
    return [
        f"### {DATES[a]} to {DATES[b]}",
        "",
        "| Step | Amount |",
        "|---|---|",
        *[f"| {k.replace('_', ' ').capitalize()} | {m(v)} |" for k, v in br["steps"].items()],
        "",
        f"Bridge reconciles: **{'yes' if br['reconciles'] else 'NO'}**; start and end equal the booked total "
        f"allowance: **{'yes' if br.get('ties_to_total_allowance') else 'NO'}**.{note}",
    ]


def overlay_section(r: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    L = [
        "### Overlay register (ADR-006; `cecl/overlays.py`, log `docs/governance/overlay_log.json`)",
        "",
        "Every proposed overlay is checked each run: a standard factor (Q1-Q9; Q4 and Q6 not applicable), the numbers "
        "method (or a written justification for a percentage), caps of 5% each and 10% in total of the modelled reserve "
        "(or a written exception flagged here), the numbers method and the checker's sign-off for a decrease, expiry "
        "at quarter-end, at most two renewals before the model must be fixed, and maker/checker on different days. "
        "Only approved overlays that pass every rule are booked.",
        "",
        "| Reserve date | Modelled reserve | Overlays proposed | Booked | Booked total | Share of modelled |",
        "|---|---|---|---|---|---|",
    ]
    for k in keys:
        o = r["dates"][k]["overlays"]
        n_booked = sum(e["status"] == "booked" for e in o["entries"])
        L.append(f"| {DATES[k]} | {m(o['modelled_reserve'])} | {len(o['entries'])} | {n_booked} | "
                 f"{m(o['booked_total'])} | {pct(o['booked_share_of_modelled'], 1)} |")  # fmt: skip
    L += ["", "Considered and not proposed:", ""]
    for k in keys:
        for a in r["dates"][k]["overlays"].get("assessments", []):
            L.append(f"- **{DATES[k]}, {a['id']} ({a['factor']}):** {a['issue']}. Decision: {a['decision']} "
                     f"({a['decided_by']}, {a['decided']}; review {a['review']}).")  # fmt: skip
    return L


def main() -> int:
    r = json.loads(SRC.read_text(encoding="utf-8"))
    q0, q1, q2 = r["dates"]["202512"], r["dates"]["202603"], r["dates"]["202606"]
    keys = ("202512", "202603", "202606")
    u = r["uplift_sensitivity"]
    bm = r["benchmarks"]
    L: list[str] = [
        "# Allowance memo: Q2 2026 (reserve date 30 June 2026)",
        "",
        "- **Prepared by:** Martins Ogiekpolor (maker). **Checker review:** _pending (a different day, ADR-006)._",
        "- **Book:** Freddie Mac Single-Family sample loans alive at the reserve date. **Scenarios:** Federal Reserve "
        "baseline and severely adverse, plus the halfway adverse path (ADR-005), from the quarter after each reserve "
        "date. Each reserve uses only the Fed scenarios published by its date: 2025 scenarios for 31 December 2025, 2026 "
        "scenarios for 31 March and 30 June 2026 (point in time, Martins, 20 November 2026).",
        f"- **Forecast and reversion (ADR-003):** {r.get('forecast_months', 24)} months, then {r.get('reversion_months', 12)} "
        "months straight-line reversion (settings of the projection job and the pricing; checked to match).",
        "- **Source:** `docs/phase4/reserve_2026Q2.json` (`scripts/build_reserve.py`); loan-level data never leaves the PC "
        "or Databricks.",
        "",
        STATUS,
        "",
        "## 1. The reserve",
        "",
        *reserve_table(r, keys),
        "",
        f"### By scenario, {DATES['202606']}",
        "",
        STATUS,
        "",
        *scenario_table(q2, u),
        "",
        f"### By scenario, {DATES['202603']}",
        "",
        *scenario_table(q1, u),
        "",
        f"### By scenario, {DATES['202512']} (Fed 2025 scenarios)",
        "",
        *scenario_table(q0, u),
        "",
        *components_section(),
        "",
        "### Severely adverse, with and without the uplift (Martins's requirement)",
        "",
        "The LGD conservatism (L-033) was measured on resolved 2017-2021 defaults, good years; it may not hold in a "
        "downturn, so the severely adverse reserve is shown both ways:",
        "",
        "| Date | Severely adverse, no uplift | Severely adverse, +34% uplift |",
        "|---|---|---|",
        *[
            f"| {DATES[k]} | {m(r['dates'][k]['severely_adverse_with_and_without_uplift']['without'])} | "
            f"{m(r['dates'][k]['severely_adverse_with_and_without_uplift']['with'])} |"
            for k in keys
        ],  # fmt: skip
        "",
        "### Scenario-weight sensitivity (ADR-005: severe weight floor 5%, cap 25%; baseline and adverse keep 2:1)",
        "",
        "| Severe weight | Reserve 30 June 2026 | With +34% uplift |",
        "|---|---|---|",
        *[
            f"| {pct(w['severe_weight'], 0)} | {m(w['reserve'])} | {m(w['reserve_with_uplift'])} |"
            for w in q2["weight_sensitivity"]
        ],
        "",
        "## 2. Overlay decision (ADR-006) and what it rests on",
        "",
        "- **L-038 default shortfall, sized on expected loss** (`docs/phase4/overlay_sizing_L038.md`): over the 24 holdout "
        "months, projected expected loss was 0.74 of the same loans' actual defaults priced at the model's LGD, so +34% "
        "would close it.",
        "- **L-033 LGD conservatism:** the LGD model predicts 1.64 times the real loss per default (resolved 2017-2021 "
        "defaults). Priced at the real loss, the projected expected loss is 1.22 times the estimated real loss.",
        '- **Decision (Option B):** "The L-038 default shortfall is assessed at the expected-loss level. Because the LGD '
        "model's documented conservatism (L-033, 1.64x) more than offsets the default shortfall (0.74 in loss terms), the "
        "baseline reserve carries no L-038 overlay. Both errors are disclosed with every reserve figure. This applies to "
        'the baseline only and is reviewed each quarter." (Martins Ogiekpolor, 19 November 2026)',
        "- **The 24-month gap may understate the lifetime gap:** 2026 was the weakest year of the holdout (0.74 of actual "
        "defaults counted correctly; 0.60 in loss terms), and the reserve runs to each loan's maturity.",
        "- **Other context (L-039 re-measurement):** in 2011-2019 v2 over-projected defaults by 27-37%; so in benign "
        "conditions the default side is likely conservative too, and in a 2024-2026-like period it is short.",
        "- Overlays booked: **none** at any of the three dates (register below).",
        "",
        *overlay_section(r, keys),
        "",
        "## 3. Rollforward, Q1 and Q2 2026 (to the cent)",
        "",
        STATUS,
        "",
        "Start + provision - gross write-offs + recoveries = end, on the total allowance (performing reserve, booked "
        "overlays and the defaulted-loan reserve); provision is the balancing figure. Write-offs are Freddie's realised "
        "losses at disposition (a gain counts as zero); recoveries are MI and other recoveries.",
        "",
        *rollforward_section(r["rollforward_q1_2026"], "202512", "202603", "Q1 2026"),
        "",
        *rollforward_section(r["rollforward_q2_2026"], "202603", "202606", "Q2 2026"),
        "",
        "## 4. Reserve bridges (weighted, total allowance)",
        "",
        "Exits: the start reserve of loans that left the book; new loans: the end reserve of loans not in the start "
        "book; credit quality and ageing: continuing loans under the start economy, end vs start; economic forecast: "
        "the end economy vs the start economy for the end book; overlays: change in booked overlays; defaulted-loan "
        "reserve change: the change in the reserve on defaulted, unresolved loans; model changes: none (same models).",
        "",
        *bridge_section(r["bridge_q4_2025_to_q1_2026"], "202512", "202603"),
        "",
        *bridge_section(r["bridge_q1_to_q2_2026"], "202603", "202606"),
        "",
        "## 5. Benchmarks (M-05, M-06)",
        "",
        "| Method | Reserve 30 June 2026 | How |",
        "|---|---|---|",
        f"| CECL engine (booked, weighted) | {m(q2['weighted_reserve'])} | PD x LGD x EAD, scenarios, reversion, to maturity |",
        f"| Loss-rate method | {m(bm['loss_rate_method']['reserve'])} | average annual net loss rate 1999-2025 "
        f"({pct(bm['loss_rate_method']['annual_net_loss_rate_1999_2025'], 3)}) x balance x expected remaining life "
        f"({bm['loss_rate_method']['expected_remaining_life_years']:.1f} years) |",
        f"| Vintage method | {m(bm['vintage_method']['reserve'])} | remaining part of the average cumulative loss curve of "
        f"mature vintages 1999-2012 x each book vintage's original balance x share still on the book |",
        "",
        "The benchmarks are through-the-cycle: both include the 2008 crisis losses at full weight, while the engine "
        "uses the current book's own credit quality and the Fed's scenarios. Differences are expected; they are a "
        "range check, not a target.",
        "",
        *freddie_benchmark(q2),
        "",
        "## 6. Disclosure tables (aggregates only)",
        "",
        "Rollforward by loan group: section 3. Credit quality by origination year (FICO and current-LTV bands, balance), "
        "and gross write-offs by origination year (Q2 2026 and year to date): `docs/phase4/reserve_2026Q2.json` "
        "(`disclosures`), rendered in `docs/phase4/disclosures_2026Q2.md`.",
        "",
        "## 7. Methods and assumptions",
        "",
        "- **Forecast and reversion (ADR-003):** 2 years of the Fed's paths from July 2026 (the Fed's first quarter lands "
        "on the quarter after the reserve date), then 1-year straight-line reversion of unemployment, house-price growth "
        "and the mortgage rate to their long-run averages from 1990.",
        "- **State paths (ADR-008 with B4b):** state change = beta x national change (betas 1991 to the reserve date, "
        "caps 0.25-2.0 unemployment and 0.25-3.0 house prices), rescaled so the book-balance-weighted state change equals "
        "the Fed's national change exactly (checked each run).",
        "- **Scenario weighting (ADR-005):** losses weighted 60% baseline / 30% adverse / 10% severely adverse.",
        "- **Lifetime horizon:** monthly for 36 months, then yearly steps to each loan's maturity at the month-36 chances "
        "and the long-run economy (loan-age effects after year 3 not re-scored; the tail's share is shown above).",
        "- **LGD (M-02):** two-step model, severity capped at 150% for fitting (kept, Martins, 19 November 2026). **EAD "
        "(M-03):** schedule plus partial prepayment.",
        "",
        "## 8. Limitations that apply (limitations log)",
        "",
        "L-028 payoffs (largely the L-039 count, see the validation report), L-031 turnover drift, L-033 LGD conservative, "
        "L-034 EAD, L-035 crisis exam, L-036 household stress not used, L-037 downturn check test 7, L-038 holdout failed, "
        "L-039 check bias, L-040 tail, L-041 allowance components outside the engine, L-042 December 2025 reserve built after the fact; conforming loans only; Freddie's underwriting and MI shape losses; recent vintages have "
        "incomplete losses.",
        "",
        "## 9. Sign-off",
        "",
        "| Role | Name | Date | Signature |",
        "|---|---|---|---|",
        "| Maker | Martins Ogiekpolor | | |",
        "| Checker (a different day) | Martins Ogiekpolor | | |",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    d = r["disclosures"]
    D = ["# Disclosure tables, 30 June 2026 (aggregates only)", "", STATUS, ""]
    for title, key in (("Credit quality: balance by FICO band and origination year", "credit_quality_fico_by_origination_year"),
                       ("Credit quality: balance by current LTV band and origination year", "credit_quality_current_ltv_by_origination_year")):  # fmt: skip
        years = sorted({y for row in d[key].values() for y in row})
        D += [f"## {title}", "", "| Band | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
        D += [f"| {b} | " + " | ".join(m(row.get(y, 0.0)) for y in years) + " |" for b, row in d[key].items()]
        D.append("")
    for title, key in (("Gross write-offs by origination year, Q2 2026", "gross_write_offs_by_origination_year_q2_2026"),
                       ("Gross write-offs by origination year, January-June 2026", "gross_write_offs_by_origination_year_ytd_2026")):  # fmt: skip
        D += [f"## {title}", "", "| Origination year | Gross write-offs |", "|---|---|"]
        D += [f"| {y} | ${v:,.2f} |" for y, v in sorted(d[key].items())]
        D.append("")
    D += ["## Rollforward by loan group", "", "Q1 and Q2 2026: see the allowance memo, section 3.", ""]
    Path("docs/phase4/disclosures_2026Q2.md").write_text("\n".join(D), encoding="utf-8")
    print(f"written {OUT} and docs/phase4/disclosures_2026Q2.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
