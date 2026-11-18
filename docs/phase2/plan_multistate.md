# Phase 2 plan, amended for the multi-state design (ADR-011)

- **Status:** proposed, 17 November 2026, for Martins's approval together with ADR-009 Amendment 1.
  **No training until both are approved.**
- **Replaces** steps 3-8 of [plan.md](plan.md) (the single default/prepayment model). Unchanged from
  plan.md: the split (ADR-009 A), the features and their checks, 1-in-25 sampling with the weights in
  every measure, the COVID sensitivity run, MLflow tracking, `@challenger` only, the allowance rules,
  and Databricks for features and training with the PC as the reference.
- **Why:** [ADR-011](../adr/011-lifetime-projection-design.md) (accepted): 98.5% of defaults come from
  loans already behind, so the model must project how today's mostly current loans move through
  delinquency, with cures modelled separately.

## The model

Each loan-month a loan is in one of five states (from the Phase 1 panel, ADR-002 unchanged):

| State | Rule (ADR-002) |
|---|---|
| current | 0 months late (stop-the-clock count) |
| 30 | 1 month late |
| 60 | 2 months late |
| 90+ = default (absorbing) | 3+ months late, or a loss event from any state (zero-balance codes 02, 03, 09; 15 if already 90+), or a modification while 60+ days late |
| prepaid (absorbing) | paid off |

Flagged payment pauses freeze the state (stop-the-clock: the month counts as a stay); `XX` carried
forward up to 3 months (ADR-002 E); censored months are left out (outcome unknown).

| Model | Moves predicted (next month) | Families |
|---|---|---|
| T-current | current, 30, prepaid, default (loss event only) | logistic (benchmark); **LightGBM and EBM challengers** |
| T-30 | current (cure), 30, 60, prepaid, default | logistic |
| T-60 | current (cure), 30, 60, default, prepaid | logistic |

Each is one multinomial logistic model (LightGBM and the EBM: one model per destination, combined as
now). The features are the 19 table features (with "months late last month" replaced by the starting
state) plus the optional ADR-010 extras only if the evidence supports them. Cures get the macro inputs
(state house-price change, unemployment) directly, so the post-2016 rise in cures is modelled where it
happens.

**The projection** (`cecl/models/multistate.py`, new): from each loan's state, multiply the monthly
transition matrices forward, giving per month the chance of being current, 30, 60, in default or
prepaid; the engine (Phase 4) uses it with LGD and EAD and the ADR-003 macro paths.

## Data changes

- **State labels for each loan-month:** the feature table gains `state_prev` (the starting state, from
  last month's panel row) and `state` (this month), from the panel's ADR-002 statuses and stop-the-clock
  counts. One shared SQL for DuckDB and Spark, as now.
- **Sampling:** current → current months (the vast majority) are sampled 1 in 25 with exact weights per
  stratum (vintage x year x starting state); **every other move is kept** (they are rare).
- **Tie-outs:** for every vintage, moves in the table equal moves in the panel, by starting state and
  destination (weighted where sampled); contract on the new columns.
- **Horizon and projection tables:** each loan's state and inputs at the start of each period (January
  2017 for validation, July 2022 for the test) plus its actual outcomes, for the projection checks.

## Steps

| Step | What | Where | Done when |
|---|---|---|---|
| M1 | State labels and the transition feature table (both engines), tests | PC | Unit tests; a trial vintage ties out |
| M2 | 3-vintage slice on Databricks, compared row by row with the PC | Databricks | 0 differences |
| M3 | Full transition feature build | Databricks | Tie-outs and contract pass |
| M4 | Logistic T-current, T-30, T-60 (fixed settings where known; one model per job) | Databricks | 3 MLflow runs |
| M5 | LightGBM challenger for T-current, with round-by-round curves | Databricks | MLflow run |
| M6 | EBM challenger for T-current (1-in-4 sample, recorded), with the cheap learning curve (option a) | Databricks | MLflow run |
| M7 | The projection code and the 2017-2019 projection check | PC code; Databricks scoring | Projected vs actual by year and vintage |
| M8 | Selection under ADR-009 Amendment 1; validation report (states that validation informed the design); learning curves for the logistic models | PC (no compute) + small scoring | Report to Martins |
| M9 | COVID sensitivity run (all three transition models refitted with the COVID months) | Databricks | Reported next to the main results |
| M10 | The out-of-time test, once, **only after Martins approves** | Databricks | Recorded whatever it shows |
| M11 | Register the transition models (`@challenger`), reason codes for current → 30, model card | PC + API | Phase 2 report |

## Databricks minutes per day (60 or less; never Monday before 13:00 Chicago or 02:30-04:00)

Estimates from the 17 November measurements (load about 0.5, fits as measured, evaluation now fast;
each run's minutes recorded):

| Day | Runs | Minutes |
|---|---|---|
| 1 | M2 slice (about 3) + M3 full build (about 15) + M4 logistic T-current (about 12) | **about 30** |
| 2 | M4 logistic T-30 and T-60 (about 5 each) + M5 LightGBM T-current (about 12) + M7 projection check (about 5) | **about 27** |
| 3 | M6 EBM T-current (fit about 50-55, evaluation and curve about 5-9) | **about 55-60** |
| 4 | M8 scoring and logistic learning curves (about 6-10) + M9 sensitivity (about 25-30; EBM only if it is chosen, then a day of its own) | **about 35-40** |
| 5 | M10 the out-of-time test, after approval (about 10) | **about 10** |

Total about 160-175 minutes over five days, none above 60.

## What Martins approves before any training

1. This amended plan.
2. ADR-009 Amendment 1 (which comparison the 2% rule applies to, the calibration bars per transition,
   the projection check, and what the out-of-time test measures).
