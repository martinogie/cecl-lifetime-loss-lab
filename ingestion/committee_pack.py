"""The quarterly committee pack, drafted from a close's aggregate record (Phase 8; roadmap v2 item 10).

    render(close_record, monitoring) -> Markdown

Every figure comes from the close record (`results/close/<R>/close.json`) and the quarter's monitoring; nothing
is typed by hand. The draft goes into `docs/governance/committee_packs/<YYYYQn>/committee_pack.md` through a
reviewed PR; the sign-off page is left blank for the maker, the checker (on a different day) and the
committee. Aggregates only.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from cecl import close

STEP_NAMES = {"start": "Start (previous quarter-end)", "exits": "Loans that left the book",
              "new_loans": "New loans", "credit_quality_and_ageing": "Credit quality and ageing",
              "economic_forecast": "Economic forecast", "model_changes": "Model changes", "overlays": "Overlays",
              "defaulted_loan_reserve_change": "Defaulted-loan reserve", "end": "End (this quarter-end)"}  # fmt: skip


def label(ym: str) -> str:
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December")  # fmt: skip
    last = {3: 31, 6: 30, 9: 30, 12: 31}[int(ym[4:])]
    return f"{last} {months[int(ym[4:]) - 1]} {ym[:4]}"


def quarter_folder(ym: str) -> str:
    return f"{ym[:4]}Q{int(ym[4:]) // 3}"


def money(x: float | str) -> str:
    v = float(x)
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def mill(x: float) -> str:
    return f"-${-x / 1e6:,.1f}m" if x < 0 else f"${x / 1e6:,.1f}m"


def render(rec: Mapping[str, Any], mon: Mapping[str, Any]) -> str:
    R, P = rec["reserve_ym"], rec["prior_ym"]
    dR, dP = rec["dates"][R], rec["dates"][P]
    rf, br, ctl = rec["rollforward"], rec["bridge"], rec["controls"]
    t = rf["total"]
    lines = [
        f"# Allowance committee pack: {label(R)}",
        "",
        f"**DRAFT for review.** Quarter-end {label(R)}; scenarios: Federal Reserve {dR['fed_scenario_year']} baseline, "
        "adverse (halfway path) and severely adverse, weighted 60/30/10 (ADR-005). Educational model on Freddie Mac "
        "conforming mortgages (sample book): not an audited bank reserve and not accounting advice. Aggregates only.",
        "",
        "## 1. The allowance",
        "",
        f"| | {label(P)} | {label(R)} |",
        "|---|---:|---:|",
        f"| Loans in the performing book | {dP['book']['loans']:,} | {dR['book']['loans']:,} |",
        f"| Balance | {mill(dP['book']['balance'])} | {mill(dR['book']['balance'])} |",
        f"| Performing reserve (weighted) | {mill(dP['weighted_reserve'])} | {mill(dR['weighted_reserve'])} |",
        f"| Defaulted-loan reserve | {mill(dP['defaulted_pipeline']['reserve'])} | {mill(dR['defaulted_pipeline']['reserve'])} |",
        f"| Overlays booked (ADR-006) | {mill(dP['overlays']['booked_total'])} | {mill(dR['overlays']['booked_total'])} |",
        f"| **Total allowance** | **{money(dP['total_allowance'])}** | **{money(dR['total_allowance'])}** |",
        f"| Allowance ratio | {dP['total_allowance_ratio']:.2%} | {dR['total_allowance_ratio']:.2%} |",
        "",
        "## 2. Rollforward (to the cent)",
        "",
        "| | Amount |",
        "|---|---:|",
        f"| Start | {money(t['start'])} |",
        f"| Provision | {money(t['provision'])} |",
        f"| Gross write-offs | {money(t['write_offs'])} |",
        f"| Recoveries | {money(t['recoveries'])} |",
        f"| **End** | **{money(t['end'])}** |",
        "",
        f"Reconciles to the cent: **{rf['reconciles_to_the_cent']}**. Write-off events: {rf.get('write_off_events', 0):,}, "
        f"of which {rf.get('estimated_write_off_events', 0):,} estimated (loss not yet reported by Freddie; trued up next "
        "quarter).",
        "",
        "| Loan group | Start | Provision | Write-offs | Recoveries | End |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            f"| {g['group']} | {money(g['start'])} | {money(g['provision'])} | {money(g['write_offs'])} | "
            f"{money(g['recoveries'])} | {money(g['end'])} |"
            for g in rf["by_group"]
        ],
        "",
        "## 3. Reserve bridge (why it moved)",
        "",
        "| Step | Amount |",
        "|---|---:|",
        *[f"| {STEP_NAMES.get(k, k)} | {mill(v)} |" for k, v in br["steps"].items()],
        "",
        f"Reconciles: **{br['reconciles']}**; ties to both totals: **{br['ties_to_total_allowance']}**.",
        "",
        "## 4. Scenarios and weights",
        "",
        "| Scenario | Lifetime reserve | Weight |",
        "|---|---:|---:|",
        *[
            f"| {s.replace('_', ' ')} | {mill(v['reserve'])} | {close.WEIGHTS[s]:.0%} |"
            for s, v in dR["scenarios"].items()
        ],
        "",
        "Severe weight sensitivity (ADR-005 floor 5%, cap 25%):",
        "",
        "| Severe weight | Performing reserve |",
        "|---:|---:|",
        *[f"| {w['severe_weight']:.0%} | {mill(w['reserve'])} |" for w in dR["weight_sensitivity"]],
        "",
        "## 5. Overlays (ADR-006)",
        "",
        f"Booked: {mill(dR['overlays']['booked_total'])}. "
        + (
            "; ".join(
                f"{a.get('id', '')}: {a.get('decision', a.get('status', ''))}"
                for a in dR["overlays"].get("assessments", [])
            )
            or "No overlay assessments for this quarter-end."
        ),
        "",
        "## 6. Controls",
        "",
        "| Control | Result |",
        "|---|---|",
        *[
            f"| {k.replace('_', ' ')} | {'pass' if v else '**FAIL**'} |"
            for k, v in ctl.items()
            if k != "all_pass"
        ],
        "",
        "## 7. Model monitoring",
        "",
        f"Judged default cells outside 0.80-1.25: **{mon.get('default_cells_failed', 0)}**. "
        f"Retraining due: **{(mon.get('retrain') or {}).get('due', False)}**"
        + (
            f" ({'; '.join((mon.get('retrain') or {}).get('reasons', []))})"
            if (mon.get("retrain") or {}).get("due")
            else ""
        )
        + ".",
        "",
        *([f"- {a}" for a in mon.get("alerts", [])] or ["- No judged cell outside the bar."]),
        "",
        "## 8. Earlier reserves against the losses since (live rolling backtest)",
        "",
        "| Quarter-end | Allowance | Months since | Net write-offs since | Share used |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {label(a['reserve_ym'])} | {mill(a['total_allowance'])} | {a['months_elapsed']} | "
            f"{mill(a['net_write_offs_since'])} | {a['share_of_reserve_used']:.1%} |"
            for a in rec.get("reserve_adequacy", [])
        ],
        "",
        "A lifetime reserve is only fully tested once its loans have run off: the share used is shown, not judged early.",
        "",
        "## 9. Reproducibility (quarter-end snapshot)",
        "",
        f"- Code: `{rec['snapshot'].get('code_version', 'unknown')}` (git tag `cecl-{quarter_folder(R)}-close` at merge)",
        *[f"- Delta `{k}` version {v}" for k, v in rec["snapshot"].get("delta_versions", {}).items()],
        *[
            f"- Model `{k}` v{v['version']} (@{v['alias']}, run `{v['run_id'][:8]}`)"
            for k, v in rec["snapshot"].get("pd_models", {}).items()
        ],
        f"- LGD model sha256 `{rec['snapshot'].get('lgd_model_sha256', '')[:16]}`; overlay log sha256 "
        f"`{rec['snapshot'].get('overlay_log_sha256', '')[:16]}`",
        "",
        "## 10. Limits",
        "",
        "Conforming loans only (no subprime, jumbo, commercial or cards); Freddie's underwriting and mortgage insurance "
        "shape losses; recent vintages have incomplete losses; v2 default model failed its holdout (L-038) and the LGD "
        "model is conservative (L-033); the full list is in the limitations log.",
        "",
        "## 11. Sign-off",
        "",
        "| Role | Name | Date | Signature |",
        "|---|---|---|---|",
        "| Maker (prepared the close) | | | |",
        "| Checker (reviewed; a different day from the maker) | | | |",
        "| Allowance committee (approved the allowance) | | | |",
        "",
    ]
    assert Decimal(t["start"]) + Decimal(t["provision"]) + Decimal(t["write_offs"]) + Decimal(t["recoveries"]) == Decimal(t["end"]) \
        or not rf["reconciles_to_the_cent"]  # fmt: skip
    return "\n".join(lines)
