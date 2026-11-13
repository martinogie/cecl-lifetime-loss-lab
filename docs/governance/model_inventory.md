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
| M-01 | Lifetime PD and prepayment (competing-risks hazard) | Monthly chance of default and of prepayment for each loan over its life | 1: the main driver of the reserve; three model families | not started | `<catalog>.ml.pd_hazard` (`@challenger`, `@champion`) | 2 |
| M-02 | LGD (two-step) | Chance of any loss once defaulted, then the size of the loss; tied to house prices | 1: directly multiplies the reserve | not started | `<catalog>.ml.lgd` | 3 |
| M-03 | EAD (balance projection) | Balance owed at each future month, from the payment schedule and prepayment | 2: mostly mechanical for fixed-rate loans | not started | code in `cecl.ead` | 3 |
| M-04 | CECL engine (aggregation, forecast, reversion, scenario weighting) | Combines M-01 to M-03 into each loan's and the book's lifetime expected loss | 1: produces the reported reserve | not started | code in `cecl.engine` | 4 |
| M-05 | Benchmark: loss-rate method | Simple reserve estimate used as a check on M-04 | 3 | not started | code in `cecl.benchmarks` | 4 |
| M-06 | Benchmark: vintage method | Second simple check on M-04 | 3 | not started | code in `cecl.benchmarks` | 4 |
| M-07 | Adverse scenario construction (ADR-005) | Builds the middle economic path between the Fed's baseline and severely adverse | 2: feeds M-04 through the weights | not started | code in `cecl.scenarios` | 4 |
