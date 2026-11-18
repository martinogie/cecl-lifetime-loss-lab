# Phase 2 plan, amended for the multi-state design (ADR-011)

- **Status:** proposed, 17 November 2026 (revised the same day for 9 models and no daily minute cap),
  for Martins's approval together with ADR-009 Amendment 1. **No training until both are approved.**
- **Replaces** steps 3-8 of [plan.md](plan.md) (the single default/prepayment model). Unchanged from
  plan.md: the split (ADR-009 A), the features and their checks, weights in every measure, the COVID
  sensitivity run, MLflow tracking, `@challenger` only, the blocked windows, and Databricks for
  features and training with the PC as the reference.
- **Why:** [ADR-011](../adr/011-lifetime-projection-design.md) (accepted): 98.5% of defaults come from
  loans already behind, so the model must project how today's mostly current loans move through
  delinquency, with cures modelled separately; all three families compete at every step.

## The model

Each loan-month a loan is in one of five states (from the Phase 1 panel; ADR-002 unchanged):

| State | Rule (ADR-002) |
|---|---|
| current | 0 months late (stop-the-clock count) |
| 30 | 1 month late |
| 60 | 2 months late |
| 90+ = default (absorbing) | 3+ months late, or a loss event from any state (zero-balance codes 02, 03, 09; 15 if already 90+), or a modification while 60+ days late |
| prepaid (absorbing) | paid off |

Flagged payment pauses freeze the state (the month counts as a stay); `XX` is carried forward up to
3 months (ADR-002 E); censored months are left out (outcome unknown). A loan's first month starts in
current (a new loan; `XX` at age 0 counts as current, L-017).

**Nine models: 3 transitions x 3 families**, each trained only on loan-months that start in its state:

| Transition model | Destinations (next month) | Families |
|---|---|---|
| T-current | current, 30, 60, default, prepaid | logistic, LightGBM, EBM |
| T-30 | current (cure), 30, 60, default, prepaid | logistic, LightGBM, EBM |
| T-60 | current (cure), 30, 60, default, prepaid | logistic, LightGBM, EBM |

- **Logistic:** one multinomial scorecard model per transition (as now). **LightGBM and EBM:** one
  binary model per destination, combined into probabilities that add to 1 (as now). A destination
  with fewer than 50 training moves (for example current -> 60, which happens only when reporting
  skips a month) gets its training rate as a constant instead of a model; this is logged.
- **Features:** the 19 table features, minus "months late last month" (it is the starting state, so
  constant within a transition model; 18 remain, including months late in the past 12 and last
  month's payment-pause flag); the macro inputs (current LTV from state house prices, unemployment and
  its 12-month change, rate incentive) enter every transition, so cures respond to the economy. The optional ADR-010 extras only if the evidence supports them, with Martins's approval.
- **The projection** (`cecl/models/multistate.py`, new): from each loan's state, apply the monthly
  transition probabilities forward (the economy and loan age moving each month), giving per month the
  chance of being current, 30, 60, in default or prepaid. Phase 4's engine uses it with LGD, EAD and
  the ADR-003 macro paths.
- **Selection:** ADR-009 Amendment 1, per transition, then the combination check.

## Data changes

- **State labels for every loan-month:** the feature table gains `state_prev` (the starting state)
  and `state` (this month's state), from the panel's ADR-002 statuses and stop-the-clock counts. One
  shared SQL for DuckDB and Spark, as now.
- **Sampling:** months starting **current** that stay current are sampled **1 in 25** with exact
  weights per stratum (vintage x year); every move out of current is kept. **Every 30 and 60 row is
  kept** (practical: in the sample panel, 695 thousand loan-months start 30 days late and 145
  thousand start 60 days late, against 71.5 million starting current).
- **Tie-outs (every vintage, a mismatch stops the build):**
  1. every live loan-month is in exactly one starting state (current + 30 + 60 = the panel's live
     loan-months, weighted where sampled);
  2. the moves add up to the panel's outcomes: moves into default = the panel's first defaults; moves
     into prepaid = the panel's prepayments; moves by starting state and destination equal the
     panel's (weighted where sampled);
  3. contract on the new columns (allowed states, no starting state of default or prepaid).
- **Projection tables:** each loan alive at the start of the period with its state and inputs
  (January 2017 for validation; July 2022 for the test, not touched until approval), the monthly
  macro inputs as they happened, and the actual outcomes, for the projection checks.

## Training curves and fit table (every one of the 9 models)

- **LightGBM:** training and validation log loss round by round, and the best round (the fixed 400
  rounds stay; no early stopping on 2017-2019).
- **Logistic:** learning curve at 10/25/50/100% of the training rows.
- **EBM:** learning curve, option (a): fits at 10% and 25% with one outer bag, plus the full model.
- **Fit table** for each model: training vs validation log loss, the gap, and an over/underfitting
  verdict; Gini next to every AUC. Logged to MLflow with the plots.

## Steps

| Step | What | Where | Done when |
|---|---|---|---|
| M1 | State labels, the transition feature table and its tie-outs (both engines); per-transition training and evaluation code; projection code; tests | PC | Unit tests; one vintage ties out on the PC |
| M2 | 3-vintage slice on Databricks, compared row by row with the PC | Databricks | 0 differences |
| M3 | Full transition feature build and projection tables | Databricks | Tie-outs and contract pass |
| M4 | **The 9 models, one per job, back to back** (order below) | Databricks | 9 MLflow runs with curves and fit tables |
| — | **Report to Martins** (all 9 done, or a cut-off stopped them) | | |
| M5 | Selection per transition (ADR-009 Amendment 1); the 2017-2019 projection check of the chosen and all-logistic combinations; the combination check | PC + Databricks scoring | Selection table and projection check |
| M6 | COVID sensitivity run: the three chosen models refitted with the COVID months | Databricks | Reported next to the main results |
| M7 | Validation report (says validation informed the design) | PC | **Report to Martins before the test** |
| M8 | The out-of-time test, once, **only after Martins approves** | Databricks | Recorded whatever it shows |
| M9 | Register the combination (`@challenger`), reason codes per step, model card, inventory | PC + API | Phase 2 report |

**Job order for M4** (shortest first, so a cut-off hits the longest last): T-60 logistic, LightGBM,
EBM; T-30 logistic, LightGBM, EBM; T-current logistic, LightGBM, EBM.

## Databricks minutes (no daily cap)

- Jobs run **back to back**, one model per job, each from a clean commit, each job's minutes recorded
  in the run log. Never on a Monday before 13:00 Chicago or between 02:30 and 04:00 (both enforced in
  `cecl/scheduling.py`). If Databricks cuts off for the day, the remaining jobs resume the next day.
- Estimates from the 17 November measurements (evaluation now fast):

| Work | Minutes |
|---|---|
| M2 slice + M3 full build and projection tables | about 20-25 |
| T-60: logistic, LightGBM, EBM (about 100 thousand training rows) | about 10-15 |
| T-30: logistic, LightGBM, EBM (about 500 thousand training rows, EBM on all of them) | about 20-30 |
| T-current: logistic (about 12), LightGBM (about 12), EBM (about 55-60 on 1 in 4, recorded) | about 80-85 |
| M5 projection checks and scoring | about 5-10 |
| M6 COVID sensitivity (the 3 chosen models; longer if EBM wins T-current) | about 15-60 |
| M8 the out-of-time test, after approval | about 10 |
| **Total** | **about 160-235** |

## What Martins approves before any training

1. This amended plan.
2. ADR-009 Amendment 1 (the rule per transition, calibration bars per transition with the 200-move
   minimum, how mixed winners are documented, and what the out-of-time test measures).
