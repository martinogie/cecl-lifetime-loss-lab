# Validation pack: CECL Lifetime Loss Lab (for an independent reviewer)

- **Prepared by:** Martins Ogiekpolor (developer and owner), 20 November 2026.
- **Reviewer:** _to be completed by the reviewer_ (in this project the checker is Martins, in writing on a
  different day; there is no independent validation team, L-011).
- **Format:** the Bank Health validation-pack format. Scope, what was tested, the evidence, open findings,
  and a sign-off. Every figure links to a file in the repository. Loan-level data never leaves the PC or
  the private Databricks workspace; only aggregates are here.

Educational model on Freddie Mac conforming mortgages (sample files); not an audited bank reserve and not
accounting advice.

## 1. Scope

| Item | What it is | Where |
|---|---|---|
| Purpose | A bank-style CECL allowance for a mortgage book: lifetime PD (with payoffs as a competing outcome) x LGD x EAD, Fed scenarios, a reasonable-and-supportable forecast with reversion, scenario weighting, overlays under rules, a rollforward and a bridge | `README.md`, roadmap v2 (private) |
| Models in scope | M-01 to M-07 | `docs/governance/model_inventory.md`, `docs/model_card.md` |
| Booked figure | Total allowance at 30 June 2026: $297.5m (0.35% of a $84.6bn sample book); 31 March 2026 $290.1m; 31 December 2025 $328.2m | `docs/allowance_memos/2026Q2_allowance_memo.md`, `docs/phase4/reserve_2026Q2.json` |
| Out of scope | Whole-bank revenue, expense and capital; Basel capital; non-conforming, jumbo, commercial and card loans | CLAUDE.md "Scope" |

## 2. Model status the reviewer should know first

- **M-01 v2 is `@challenger`, and it failed its holdout** (July 2024 - June 2026, run once): 0.81 of actual
  defaults under the recorded check, 0.92 counted correctly, with 5 (recorded) or 3 (corrected) of 8 judged
  cells outside 0.80-1.25 (L-038).
- **The check that measured the exams was biased low** (L-039). Counted correctly, v2 over-projects
  defaults by 27-37% in 2011-2019 and falls short in 2024-2026.
- **Two validation rules were changed after their results** (ADR-009 Amendments 4 and 5); the original
  failures stay on record (L-035, L-037). The holdout rules were then locked.
- **No overlay is booked** (Option B): the LGD's conservatism (L-033, 1.64x) more than offsets the default
  shortfall (0.74 in loss terms). Both errors are disclosed with every figure, and the +34% uplift is shown
  as a sensitivity.

## 3. What was tested (each rule committed before the test ran)

| Test | Rule committed | Result | Evidence |
|---|---|---|---|
| Reproduction of Freddie's summary statistics | Phase 1 | passed | `docs/phase1/` |
| Data contracts and tie-outs on every run | Phase 0-1 | pass on every run | run log, `docs/governance/controls_matrix.md` |
| PD selection (2% rule, calibration bars) | ADR-009 | logistic at all steps | validation report, Phase 2 |
| v1 out-of-time test (run once) | ADR-009 | failed (L-032) | validation report |
| v2 walk-forward exams | ADR-009 Amendment 3 | F2-F4 pass (recorded count); crisis F1 failed | validation report |
| v2 downturn-sensitivity check | Amendment 4 (thresholds before code) | passed after Amendment 5 (L-037) | validation report |
| v2 holdout (run once) | Amendment 3 E, locked by Amendment 5 | **failed** (L-038) | validation report |
| L-039 re-measurement (for the record) | Martins, 19 Nov 2026 | corrected counts reported | validation report |
| LGD check, EAD check | Phase 3 | LGD conservative 1.64x (L-033); EAD within 2% | `docs/phase3/` |
| Reserve tie-outs, rollforwards to the cent, bridges | Phase 4 | pass (Q1 and Q2 2026) | allowance memo |
| Stress and sensitivity | Phase 5 plan and decisions | severely adverse 9-quarter loss rate 1.19% vs DFAST 1.5% (0.9-2.8%); scenario weights move the reserve most | `docs/phase5/stress_report.md` |
| **Crisis backtest** (run once) | `docs/phase6/test_designs.md` CB | see results | `docs/phase6/validation_results.md` §1 |
| **Rolling backtest** (run once) | RB | see results | §2 |
| **Forbearance test** (run once) | FT | see results | §3 |
| **Peer comparison** | Peer rule (a range, not a target) | see results | §4 |
| **L-043 rate-shock breakdown** | L-043 rule | see results | §5 |

Martins's written expectations for the Phase 6 tests were committed before they ran:
`docs/phase6/backtest_expectations.md`. The results page sets each result next to its expectation.

## 4. Open findings for the reviewer

The limitations log (`docs/governance/limitations_log.md`) is the full list. Each item has an owner and a
fix date. The ones that matter most for the booked figure:

| ID | Finding | Effect on the reserve | Fix plan |
|---|---|---|---|
| L-038 | v2 failed its holdout: defaults short in 2024-2026, worst in the newest vintages | Understates the default side; offset by L-033 under Option B | v3: cure-rate and 60-day lingering fix |
| L-033 | The LGD predicts 1.64x the real loss per default | Overstates | v3: LGD recalibration; simple LGD challenger |
| L-039 | The holdout-style check was biased low | Measurement only | Corrected count used from Phase 4 on |
| L-035 | Crisis exam failed | Unknown in a crisis | Information; see the crisis backtest |
| L-040 | The tail after year 3 is simplified | Small (8% of the baseline reserve is tail) | v3 |
| L-041 | Defaulted-loan reserve, estimated write-offs, benchmark gap | Small | Q3 true-up |
| L-042 | December 2025 reserve built after the fact | Small | Quarter-end snapshots from the first live close |
| L-043 | Rate shocks lower the reserve in both directions | Small (under 3%) | See the breakdown |
| L-011 | No independent validation team | Governance | Outside reviewer using this pack |

## 5. Reviewer's checklist

- [ ] The test rules were committed before each test (check the git history: rules, then expectations, then
      results).
- [ ] Every figure in the allowance memo and the results page traces to a JSON file and a script.
- [ ] The rollforward reconciles to the cent, and the bridge ties to the booked allowance, for both quarters.
- [ ] The limitations log has an owner and a fix date for every open item.
- [ ] No loan-level data is in the repository (only aggregates).
- [ ] The conclusion below is supported by the evidence.

## 6. Developer's conclusion (for the reviewer to challenge)

_Written after the Phase 6 results; see below._

## 7. Sign-off

| Role | Name | Date | Signature / comments |
|---|---|---|---|
| Developer (maker) | Martins Ogiekpolor | | |
| Checker (a different day) | Martins Ogiekpolor | | |
| Independent reviewer | | | |
| Model tiers confirmed (inventory) | Martins Ogiekpolor | | |
