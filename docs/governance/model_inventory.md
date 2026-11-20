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
| M-01 | Lifetime PD and prepayment (competing-risks hazard) | Monthly chance of default and of prepayment for each loan over its life | 1: the main driver of the reserve; three model families | @challenger (19 Nov 2026): multi-state logistic transition models; **failed the out-of-time test** (L-032); not champion. Version 2 (ADR-009 Amendment 3, code `c160e46`) failed the walk-forward crisis exam (L-035): stopped before its holdout, not registered | `<catalog>.ml.pd_transition_current`, `_30`, `_60` (`@challenger`) | 2 |
| M-02 | LGD (two-step) | Chance of any loss once defaulted, then the size of the loss; tied to house prices | 1: directly multiplies the reserve | draft (Phase 3, 19 Nov 2026): two-step monotone trees on every ADR-002 default; docs/phase3/model_documentation.md; L-033 | `<catalog>.ml.lgd` (not registered yet) | 3 |
| M-03 | EAD (balance projection) | Balance owed at each future month, from the payment schedule and prepayment | 2: mostly mechanical for fixed-rate loans | draft (Phase 3, 19 Nov 2026): schedule plus partial prepayment by loan age; within 2% on held-out loans; L-034 | code in `cecl.ead` | 3 |
| M-04 | CECL engine (aggregation, forecast, reversion, scenario weighting) | Combines M-01 to M-03 into each loan's and the book's lifetime expected loss | 1: produces the reported reserve | not started | code in `cecl.engine` | 4 |
| M-05 | Benchmark: loss-rate method | Simple reserve estimate used as a check on M-04 | 3 | not started | code in `cecl.benchmarks` | 4 |
| M-06 | Benchmark: vintage method | Second simple check on M-04 | 3 | not started | code in `cecl.benchmarks` | 4 |
| M-07 | Adverse scenario construction (ADR-005) | Builds the middle economic path between the Fed's baseline and severely adverse | 2: feeds M-04 through the weights | not started | code in `cecl.scenarios` | 4 |

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
