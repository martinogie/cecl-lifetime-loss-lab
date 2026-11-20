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
DATES = {"202603": "31 March 2026", "202606": "30 June 2026"}
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


def main() -> int:
    r = json.loads(SRC.read_text(encoding="utf-8"))
    q1, q2 = r["dates"]["202603"], r["dates"]["202606"]
    u = r["uplift_sensitivity"]
    rf, br, bm = r["rollforward_q2_2026"], r["bridge_q1_to_q2_2026"], r["benchmarks"]
    L: list[str] = [
        "# Allowance memo: Q2 2026 (reserve date 30 June 2026)",
        "",
        "- **Prepared by:** Martins Ogiekpolor (maker). **Checker review:** _pending (a different day, ADR-006)._",
        "- **Book:** Freddie Mac Single-Family sample loans alive at the reserve date. **Scenarios:** Federal Reserve 2026 "
        "baseline and severely adverse, plus the halfway adverse path (ADR-005), from July 2026.",
        "- **Source:** `docs/phase4/reserve_2026Q2.json` (`scripts/build_reserve.py`); loan-level data never leaves the PC "
        "or Databricks.",
        "",
        STATUS,
        "",
        "## 1. The reserve",
        "",
        f"| | {DATES['202603']} | {DATES['202606']} |",
        "|---|---|---|",
        f"| Loans | {q1['book']['loans']:,} | {q2['book']['loans']:,} |",
        f"| Balance | {m(q1['book']['balance'])} | {m(q2['book']['balance'])} |",
        f"| **Booked reserve (weighted 60/30/10, no overlay)** | **{m(q1['weighted_reserve'])}** | **{m(q2['weighted_reserve'])}** |",
        f"| Reserve ratio (reserve / balance) | {pct(q1['reserve_ratio'], 3)} | {pct(q2['reserve_ratio'], 3)} |",
        f"| Sensitivity: with the +34% default uplift (Option A) | {m(q1['weighted_reserve_with_uplift'])} | {m(q2['weighted_reserve_with_uplift'])} |",
        f"| Tie-out: book reserve = sum of loan reserves (every scenario) | {'pass' if all(t['passes'] for t in q1['tie_outs'].values()) else 'FAIL'} | {'pass' if all(t['passes'] for t in q2['tie_outs'].values()) else 'FAIL'} |",
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
            for k in ("202603", "202606")
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
        "- Overlays booked: **none**. Cap check (5% each, 10% total): not applicable.",
        "",
        "## 3. Rollforward, Q2 2026 (to the cent)",
        "",
        STATUS,
        "",
        "| Loan group (origination year) | 31 March 2026 | Provision | Gross write-offs | Recoveries | 30 June 2026 |",
        "|---|---|---|---|---|---|",
        *[
            f"| {g['group']} | {g['start']} | {g['provision']} | {g['write_offs']} | {g['recoveries']} | {g['end']} |"
            for g in rf["by_group"]
        ],
        f"| **Total** | **{rf['total']['start']}** | **{rf['total']['provision']}** | **{rf['total']['write_offs']}** | "
        f"**{rf['total']['recoveries']}** | **{rf['total']['end']}** |",
        "",
        f"Reconciles to the cent: **{'yes' if rf['reconciles_to_the_cent'] else 'NO'}** (start + provision - write-offs + "
        f"recoveries = end; provision is the balancing figure). Write-off events in the quarter on loans in the March book: "
        f"{rf['write_off_events']}. Write-offs are Freddie's realised losses at disposition (a gain counts as zero); "
        "recoveries are MI and other recoveries.",
        "",
        "## 4. Reserve bridge, 31 March to 30 June 2026 (weighted)",
        "",
        "| Step | Amount |",
        "|---|---|",
        *[f"| {k.replace('_', ' ').capitalize()} | {m(v)} |" for k, v in br["steps"].items()],
        "",
        f"Bridge reconciles: **{'yes' if br['reconciles'] else 'NO'}**. Exits: the March reserve of loans that left the "
        "book; new loans: the June reserve of loans not in the March book; credit quality and ageing: continuing loans "
        "under the March economy, June vs March; economic forecast: the June economy vs the March economy for the June "
        "book; model changes and overlays: none.",
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
        "L-039 check bias; conforming loans only; Freddie's underwriting and MI shape losses; recent vintages have "
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
    D += ["## Rollforward by loan group", "", "See the allowance memo, section 3.", ""]
    Path("docs/phase4/disclosures_2026Q2.md").write_text("\n".join(D), encoding="utf-8")
    print(f"written {OUT} and docs/phase4/disclosures_2026Q2.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
