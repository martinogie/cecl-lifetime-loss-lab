# Phase 2 plan: PD and prepayment models

- **Status:** plan only, 16 November 2026. [ADR-009](../adr/009-model-selection-rule.md) (the
  selection rule) was decided by Martins the same day. Nothing is built until this plan is merged.
- **Source:** roadmap v2, section 4 (data, splits, known limits), section 5.1 (methods) and the
  Phase 2 acceptance criteria in section 7.
- **What Phase 2 delivers:** one model of each loan's monthly chance of **default** and of
  **prepayment** (a loan that pays off early can never default: "competing risks"), fitted in three
  families, tracked in MLflow, chosen by a rule written in advance, tested once on the most recent
  years, with plain-English reasons for each loan's PD. Phase 4 turns these monthly chances into
  lifetime losses.

## Acceptance criteria (roadmap v2, Phase 2) and where each is met

| Criterion | Where |
|---|---|
| Competing-risks hazard model in three families, tracked in MLflow | Steps 3–5, MLflow below |
| Calibration by vintage and year within agreed tolerance; AUC by horizon reported | Step 6; the tolerance is ADR-009 B |
| Out-of-time test run once; selection rule committed before it | ADR-009 (before step 6); step 7 |
| Plain-English reason codes for each loan's PD | Step 8 |

## 1. The data split (ADR-009 A, decided)

Splits are by **observation month** (when the loan-month happens), never at random: a model must be
judged on a period it has never seen. Martins's decision:

| Part | Months | Loan-months | Defaults | Prepayments | Used for |
|---|---|---|---|---|---|
| Train | 1999 – 2016 | 39.5 million | 40,305 | 576,642 | fitting all three families |
| Validate | 2017 – 2019 | 10.9 million | 3,752 | 105,672 | choosing between them (ADR-009 rule) |
| COVID period | 2020 – June 2022 | 8.4 million | 2,008 | 186,213 | not used to fit or choose; studied in Phase 6's forbearance test |
| Out-of-time test | July 2022 – June 2026 | 14.9 million | 6,648 | 100,349 | scored **once**, after ADR-009 is committed |

The test months are kept out of every table the models see until the test command runs (step 7).
Phase 6's crisis backtest refits the chosen family on years before 2007 and tests 2007–2010.

### Why leaving out the COVID period is consistent with ADR-002

ADR-002 chose **stop the clock (D2)** and rejected **dropping 2020–2021 from training (D3)**. These
answer two different questions, so excluding the COVID period here doesn't undo that choice:

- **ADR-002 is about what counts as a default.** It applies everywhere: the panel marks forbearance
  months so that a permitted payment pause is never a default, for every loan and every month,
  including 2020–2022. Nothing about that changes. D3 was rejected because it would have left the
  project with *no* treatment of forbearance: the reserve still has to handle loans in forbearance
  today, and the backtest has to test it.
- **ADR-009 is about which months each model is fitted and judged on.** The main model is fitted on
  1999–2016 and judged on 2017–2019 and July 2022 – June 2026, so its scores reflect ordinary times
  and the crisis, not a period when federal policy suspended normal payment behaviour.
- **The COVID period isn't thrown away.** It is (a) the subject of the Phase 6 forbearance test,
  which compares stop-the-clock with "no special treatment" (ADR-002 D1) on exactly these months;
  (b) used by a **sensitivity run** (below); and (c) still in the data the reserve engine starts
  from: loans that were in forbearance carry their stop-the-clock history into Phase 4.

### Sensitivity run: training with the COVID period

The chosen family is refitted on **1999–2016 plus 2020 – June 2022**, with stop-the-clock applied
(the panel's ADR-002 statuses) and the forbearance flag on as a feature, and scored on the same
validation and test months. Its results sit next to the main model's in the Phase 2 report: the same
measures, the same calibration tables. It answers "does including the COVID years change the
model?" and is never used to choose the model (ADR-009). Because it is scored on the same test
months, it runs in the same single test command (step 7), not as a second look at the test.

## 2. Features (one row per loan-month, known at the start of the month)

Every feature uses only information available at the start of month *t* (values from month *t − 1*
or earlier), so the model never sees the outcome it predicts.

| Feature | From | Why (expected direction) |
|---|---|---|
| Loan age (months) | panel | Defaults peak a few years in; prepayment rises then levels off |
| Credit score at origination | silver origination | Lower score, more defaults (monotonic) |
| **Current LTV**: balance ÷ (original value × state house-price change since origination) | panel balance, origination LTV and balance, FHFA state index (silver macro, rule M1/L1) | Higher, more defaults (monotonic); equity also enables refinancing |
| Debt-to-income at origination | silver origination | Higher, more defaults (monotonic) |
| **Rate incentive**: loan's current rate − market 30-year rate | panel rate, FRED `MORTGAGE30US` | Larger, more prepayment (monotonic); the refinance motive |
| Past delinquency: months late last month; months 30+ days late in the last 12 | panel (ADR-002 rules) | Recent late payment, far more defaults (monotonic) |
| State unemployment rate, and its 12-month change | FRED state series (silver macro) | Higher or rising, more defaults (monotonic) |
| Forbearance flag last month | panel (`flagged_pause`, ADR-002; flags exist from 2014, L-016) | Separates payment pauses from real distress |
| Loan purpose, occupancy, mortgage-insurance %, term over 360 months (L-014), original balance | silver origination | Standard risk and prepayment differences (no forced direction) |
| Calendar month | panel | Prepayment seasonality (home sales peak in summer) |

**Rows and sampling.** Every default and prepayment event is kept. Months where nothing happens are
sampled (about 1 in 25) and given a weight of 25, so the probabilities stay unbiased. That turns
73.7 million rows into about 4 million, which fits the PC (7.7 GB memory). The sampling seed and
rate are logged.

**The weights are used everywhere, not only in training.** Every measure is computed on the
weighted rows: log loss, AUC (weighted ranking), calibration (predicted and actual counts are
weighted sums, so "actual defaults" and "predicted defaults" both describe the full panel), PSI and
the reason-code averages. A unit test checks this: on a small table, the weighted measures on the
sample match the same measures computed on the full, unsampled table. The validation and test months
are sampled the same way (with their own seed), and every event in them is kept.

**Checks on the feature table** (as in Phase 1): a data contract (ranges, missing values, all states
present); tie-outs (every event in the panel is in the feature table, and weighted non-event months
add up to the panel's); and current LTV checked against Freddie's own estimated LTV (`eltv`) where
Freddie reports it.

**Sample weights (ADR-007).** The sample files hold 50,000 loans per vintage whatever the vintage's
real size. The hazard is a per-loan model, so it is fitted per loan; book-level figures (calibration
totals, Phase 4) apply the ADR-007 vintage weights.

## 3–5. The three model families

All three predict the same thing: for each loan-month, the probability of default, of prepayment,
and of neither. All use the same features, rows and split.

| Step | Family | How | Explained by |
|---|---|---|---|
| 3 | **Logistic scorecard** (the benchmark) | Multinomial logistic regression on binned features (scorecard-style bins, monotonic where expected), statsmodels or scikit-learn | Coefficients; points per bin |
| 4 | **LightGBM** | Gradient-boosted trees with monotonic constraints on the features marked monotonic above; tuned on the training years only (time-ordered folds) | SHAP values |
| 5 | **Explainable Boosting Machine** (InterpretML) | An additive model of one-feature shapes plus a few pairwise interactions | Its own shape plots |

## 6. Validation and choice (on 2017–2019 only)

For every family, all with the sampling weights: log loss, AUC at 12, 24 and 36 months from fixed
start dates, calibration (predicted ÷ actual defaults and prepayments by year and by vintage), score
stability (PSI between years), and reason codes. Then the ADR-009 rule, as decided:

- the **logistic benchmark stays** unless a challenger improves validation log loss (default) by at
  least **2%**, passes **every calibration bar** (0.80–1.25 for each year and vintage with at least
  200 defaults) and produces reason codes; ties go to the simpler family;
- **one family** covers default and prepayment (prepayment must also pass its calibration bars).

This is reported in full before the test.

## 7. The out-of-time test (once)

One command scores July 2022 – June 2026. It refuses to run unless ADR-009 is accepted and
committed, records that commit, and saves the results in MLflow and the Phase 2 report whatever they
show. If the chosen model misses the tolerance, that is recorded in the limitations log, not fixed by
tuning on the test (ADR-009 E).

## 8. Reason codes

For each loan's PD, the three features that push it furthest above the average, in plain English,
for example: "Current loan-to-value 112% (house worth less than the loan)"; "Two late payments in
the last 12 months"; "Unemployment in Nevada up 3.1 points in a year". From the logistic points or
the SHAP values or the EBM shapes, depending on the family chosen; the wording table is reviewed by
Martins.

## Where training runs, and the shared Databricks allowance

| Work | Where | Why |
|---|---|---|
| Feature table and sampling | **PC** (DuckDB over the local panel; sampled first, then joined, so disk stays small: about 1–2 GB) | No Databricks minutes; the panel is already here |
| Fitting and tuning all three families | **PC** | About 4 million rows fit in memory; uses none of the allowance shared with Bank Health (L-009) |
| MLflow tracking and the model registry | **Databricks** (the dev experiment and `cecl_lab_dev.ml.pd_hazard`, created in step 9) | Logging runs and registering a model are API calls, not compute |
| One reproduction run of the chosen family | **Databricks**, a serverless job, once, after step 7 | Shows the model rebuilds identically in the lakehouse (as step 9 did for the data) |

Rules for any Databricks compute in Phase 2:
- **Never on a Monday before 13:00 Chicago, or between 02:30 and 04:00**, unless Martins confirms
  Bank Health's run has finished (as on 16 November).
- **A smaller slice first:** three vintages, to measure the minutes, then the full run only if the
  projection fits the day's allowance.
- Every job has a timeout; the minutes are recorded in the run log (as in step 9: about 55 minutes
  for the full sample pipeline).

**Before and during every training run on the PC:**
- **The dev Airflow stack is stopped** (`docker compose ... down` in `airflow/`), so its containers
  (about 1 GB of memory and some CPU) don't compete with training; it is started again afterwards.
- **A preflight check refuses to start** unless there is enough free memory (at least 4 GB) and free
  disk (at least 5 GB: the loaders' 3 GB reserve plus room for the run's files). Both figures, the
  run's peak memory and its duration go into the run log and MLflow.

**PC limits, stated plainly:** 2 processor cores, 7.7 GB memory and about 10 GB free disk.
LightGBM and the logistic model are quick on 4 million rows; the EBM is the slowest (expect tens of
minutes per fit); tuning is kept small (a handful of settings per family, time-ordered folds). The
new packages (scikit-learn, statsmodels, LightGBM, SHAP, InterpretML, MLflow) take about 1.5 GB in
the local environment (a separate `requirements-ml.txt`, so CI stays light).

## MLflow tracking

- **Where:** the dev experiment in the Databricks workspace (`/Users/<user>/cecl-lifetime-loss-lab-dev`),
  reached from the PC through the CLI profile `cecl` (the token is never read by the code).
- **Every run logs:** family; parameters; feature list and monotonic constraints; split months;
  sampling rate and seed; the data snapshot (`cecl-2026Q2-data-v1` and its fingerprint); the git
  commit; validation measures (log loss, AUC by horizon, calibration tables, PSI); artefacts
  (calibration plots, SHAP or shape summaries, the reason-code wording table); and the model with its
  input signature.
- **Registry:** the chosen family is registered as `cecl_lab_dev.ml.pd_hazard` with the alias
  `@challenger`. `@champion` only ever moves in prod, after Martins reviews the challenger
  (`cecl/environment.py`).
- **The test run** is its own MLflow run, tagged `out_of_time_test`, linked to the ADR-009 commit.

## Steps, in order

| Step | What | Done when |
|---|---|---|
| 0 | Martins: approve this plan; write ADR-009's Decision (split, bars, rule, protocol) | ADR-009 accepted and committed |
| 1 | ML environment (`requirements-ml.txt`), MLflow connection from the PC, training preflight (memory, disk, Airflow stopped) | A test run appears in the dev experiment |
| 2 | Feature table: `cecl/features.py` (pure rules, unit-tested) and a DuckDB build; sampling; contract and tie-outs | Tie-outs pass; current LTV checked against `eltv` |
| 3–5 | The three families, each fitted on the training years and logged | Three runs in MLflow |
| 6 | Validation report and the ADR-009 choice | Report shows every family against the rule |
| 7 | The out-of-time test, once, for the chosen family, the others (for information) and the COVID sensitivity run | Results recorded, whatever they are |
| 8 | Reason codes, registry (`@challenger`), Databricks reproduction run | Reason codes for every loan; alias set; reproduction matches |
| 9 | Model documentation draft, inventory entry M-01 updated, limitations log | Phase 2 report with evidence |

## What Martins will be asked for

1. The ADR-009 Decision: done (16 November 2026).
2. Review of the reason-code wording (step 8).
3. Approval before any Databricks compute, and confirmation of the Monday rule if ever needed.
4. Approval of the Phase 2 report before the model is used in Phase 4.
