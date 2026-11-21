# Model inventory

The official list of every model in the project, its owner and how risky it is (its tier), as SR 11-7
model risk management expects. One entry per model; each entry is updated whenever the model
changes, is validated or is reviewed. Completed in Phase 6.

## Risk tiers (proposed; Martins confirms)

| Tier | Meaning | Validation and review |
|---|---|---|
| 1 (high) | Drives a reported financial figure directly, or is complex and hard to check by hand | Full validation before first use; annual review; quarterly monitoring |
| 2 (medium) | Feeds a Tier 1 model, or supports decisions without setting a reported number | Validation before first use; review every two years; monitoring |
| 3 (low) | Simple, transparent, used only as a check | Light review |

Tiering considers **materiality** (how much of the reserve depends on it), **complexity** (how hard
it is to understand and test) and **use** (what decisions rest on it).

## Entry template

| Field | Value |
|---|---|
| Model ID | |
| Name | |
| Purpose (one sentence) | |
| Used for | reserve / stress testing / benchmark / monitoring |
| Owner (accountable) | |
| Developer | |
| Independent reviewer / validator | |
| Risk tier and reason | |
| Status | in development / validated / in use / retired |
| Current version (MLflow model and alias, git tag) | |
| Inputs (data and assumptions) | |
| Outputs | |
| Method (one paragraph) | |
| Key assumptions (with ADR links) | |
| Known limitations (limitations-log IDs) | |
| Validation: date, result, report link | |
| Monitoring: what is tracked, thresholds, frequency | |
| Last review date / next review date | |
| Change history (link to change log) | |

## Entries (draft)

Pre-filled with what is known in Phase 0. Owner for all: Martins Ogiekpolor. Reviewer: Martins, as
checker, in writing; there is no separate validation team, which is itself a limitation (L-011).

| Model ID | Name | Purpose | Proposed tier | Status | Registry name | Phase |
|---|---|---|---|---|---|---|
| M-01 | Lifetime PD and prepayment (competing-risks hazard) | Monthly chance of default and of prepayment for each loan over its life | 1: the main driver of the reserve; three model families | **Version 2 `@challenger`** (19 Nov 2026, dev): logistic transition models with rate-lock and memory inputs, path simulation (code `c160e46`). Passed F2-F4; crisis exam failed and made information only (Amendment 4, after the result; L-035); downturn check passed under Amendment 5 (test 7 in points, after the result; L-037); **holdout failed** (19 Nov 2026: total 0.81, 5 default cells outside the bar; L-038); stays `@challenger`, not champion; Phase 4 uses an ADR-006 overlay. Version 1: alias retired; failed the out-of-time test (L-032), records kept | `<catalog>.ml.pd_v2_transition_current`, `_30`, `_60` (`@challenger`); v1 `<catalog>.ml.pd_transition_*` (no alias) | 2 |
| M-02 | LGD (two-step) | Chance of any loss once defaulted, then the size of the loss; tied to house prices | 1: directly multiplies the reserve | draft (Phase 3, 19 Nov 2026): two-step monotone trees on every ADR-002 default; docs/phase3/model_documentation.md; L-033 | `<catalog>.ml.lgd` (not registered yet) | 3 |
| M-03 | EAD (balance projection) | Balance owed at each future month, from the payment schedule and prepayment | 2: mostly mechanical for fixed-rate loans | draft (Phase 3, 19 Nov 2026): schedule plus partial prepayment by loan age; within 2% on held-out loans; L-034 | code in `cecl.ead` | 3 |
| M-04 | CECL engine (aggregation, forecast, reversion, scenario weighting) | Combines M-01 to M-03 into each loan's and the book's lifetime expected loss | 1: produces the reported reserve | built (Phase 4, 20 Nov 2026): first reserve at 31 March and 30 June 2026; in development, not validated (Phase 6) | `cecl/engine.py`, `cecl/scenarios.py`, `scripts/build_reserve.py` | 4 |
| M-05 | Benchmark: loss-rate method | Simple reserve estimate used as a check on M-04 | 3 | built (Phase 4): range check | `scripts/build_reserve.py` (`benchmarks`) | 4 |
| M-06 | Benchmark: vintage method | Second simple check on M-04 | 3 | built (Phase 4): range check | `scripts/build_reserve.py` (`benchmarks`) | 4 |
| M-07 | Adverse scenario construction (ADR-005) | Builds the middle economic path between the Fed's baseline and severely adverse | 2: feeds M-04 through the weights | built (Phase 4): halfway path (silver `fed_paths`), state mapping ADR-008 with B4b | `cecl/scenarios.py` | 4 |

## Notes for the next annual model review

- **M-01 (18 November 2026):** on 2017-2019 LightGBM tracked the loan-age and housing-turnover drift
  better than the chosen logistic model at the current step (payoff calibration by vintage: 5 failing
  cells vs 13), but it still fails calibration cells, so under the selection rule (ADR-009 as
  amended, unchanged) logistic stays. At the review: consider a housing-turnover input and re-compare
  the families. Limitations log L-031.
- **M-01 version 2 (19 November 2026):** logistic at all three steps (LightGBM 3.05% better at the current
  step but failing calibration cells). Walk-forward: 2011-2013, 2014-2016 and 2017-2019 pass; the 2008-2010
  crisis exam fails (0.38 vs the v1-style benchmark's 0.39). Stopped for Martins before the holdout (L-035).
  Amendment 4 (written after that result) replaced the crisis exam with a downturn-sensitivity check; the
  check failed on test 7, risk ordering as ratios (L-037). v2 not registered; v1 stays `@challenger`.
  Amendment 5 (also written after its result) measured test 7 in percentage points: the check passes; v2
  registered `@challenger` in dev, v1's alias retired. Last rule change: the holdout rules are locked.
  Holdout (July 2024 - June 2026, run once 19 November 2026): **failed**, total 0.81 but 5 of 8 judged default
  cells outside 0.80-1.25 (L-038). v2 stays `@challenger`; Phase 4 uses a documented ADR-006 overlay.
  _Correction (20 November 2026): Phase 4 books **no** overlay. Martins chose Option B on 19 November 2026,
  confirmed for all three reserve dates on 20 November (L-033 netting decision, `docs/governance/overlay_log.json`)._

## Completed entries (Phase 6, 20 November 2026)

Owner, developer and checker for every model: Martins Ogiekpolor. There is no independent validation team
(L-011). The checker review is a separate written review on a different day, and the validation pack
(`docs/phase6/validation_pack.md`) is written for an outside reviewer. The tiers are the proposed ones above;
Martins confirms them in the validation pack's sign-off.

| Field | M-01 PD and prepayment | M-02 LGD | M-03 EAD | M-04 CECL engine |
|---|---|---|---|---|
| Purpose | Monthly chance of default and of payoff per loan, from its delinquency state | Loss per $ defaulted: chance of any loss, then its size | Balance owed at each future month | Combines M-01 to M-03 into each loan's and the book's lifetime expected loss, with scenarios, reversion and weights |
| Used for | reserve, stress testing | reserve, stress testing | reserve, stress testing | reserve, stress testing |
| Tier and reason | 1: the main driver of the reserve; complex (path simulation) | 1: multiplies the reserve directly; tied to house prices | 2: mostly mechanical for fixed-rate loans | 1: produces the reported reserve |
| Status | v2 `@challenger` (dev); **holdout failed** (L-038); not champion | built, not registered; conservative (L-033) | built | built; first reserves at 31 Dec 2025, 31 Mar and 30 Jun 2026 |
| Current version | `cecl_lab_dev.ml.pd_v2_transition_{current,30,60}` v1 (runs `d0e07d92`, `995197d8`, `aafd64ee`) | `data/local/phase3/lgd_model.pkl` (Phase 3, code in `cecl/lgd.py`) | `cecl/ead.py`, rates `data/local/phase3/ead_rates.json` | `cecl/engine.py`, `cecl/scenarios.py`, `scripts/build_reserve.py` |
| Inputs | 28 loan, history and economic inputs (`cecl/features.py`) | LTV at default, house-price change, MI, judicial state, loan size, credit score, investor | note rate, remaining term, loan age, partial-prepayment rates | M-01 to M-03, the Fed scenarios (point in time), ADR-003/005/008 settings |
| Key assumptions | ADR-002, ADR-009 (Amendments 1-5), ADR-011 | Severity capped at 150% for fitting; ADR-002 | Scheduled amortisation plus curtailment by loan age | ADR-003 (24 + 12 months, settings), ADR-005 (60/30/10), ADR-006 (overlays, none booked), ADR-008 B4b |
| Known limitations | L-028, L-031, L-032, L-035, L-036, L-037, L-038, L-039, L-043 | L-033 | L-034 | L-040, L-041, L-042 |
| Validation | Phase 2 exams and holdout failed (L-038); Phase 6 (21 Nov 2026): crisis backtest fail (L-044), rolling backtest fail high (L-045), forbearance pass (`docs/phase6/validation_results.md`) | Phase 3 check 2017-2021; Phase 6 refits per date | Phase 3 held-out balances within 2% | Tie-outs every run; rollforwards to the cent; Phase 5 stress; Phase 6 backtests |
| Monitoring | default and payoff calibration by vintage each quarter; holdout-style check on each new Freddie quarter | predicted vs actual loss per $ on resolved defaults each quarter | balance tracking each quarter | rollforward, bridge and tie-outs each quarter; peer range each quarter |
| Last / next review | 20 Nov 2026 / at model v3, or by 20 Nov 2027 | 20 Nov 2026 / by 20 Nov 2027 | 20 Nov 2026 / by 20 Nov 2028 | 20 Nov 2026 / by 20 Nov 2027 |

| Field | M-05 Loss-rate benchmark | M-06 Vintage benchmark | M-07 Adverse path |
|---|---|---|---|
| Purpose | Simple reserve check: average annual net loss rate x balance x remaining life | Simple reserve check: remaining part of the average loss curve of mature vintages | The middle economic path, halfway between the Fed's baseline and severely adverse |
| Used for | benchmark | benchmark | reserve (through the weights), stress testing |
| Tier and reason | 3: a range check only | 3: a range check only | 2: feeds M-04 through the 30% weight |
| Status | built | built | built |
| Version | `scripts/build_reserve.py` (`benchmarks`) | same | `cecl/macro.py` (`fed_paths`), `cecl/scenarios.py` |
| Known limitations | L-041 (through-the-cycle, far above the engine) | L-041 | ADR-005 (halfway, not a Fed scenario) |
| Validation | Reproduced from Freddie's loss fields | same | B4b check every run (book-weighted state change = national) |
| Last / next review | 20 Nov 2026 / by 20 Nov 2028 | 20 Nov 2026 / by 20 Nov 2028 | 20 Nov 2026 / each February, with the Fed's new scenarios |
