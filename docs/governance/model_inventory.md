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
