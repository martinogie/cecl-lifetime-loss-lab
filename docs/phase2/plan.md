# Phase 2 plan: PD and prepayment models

- **Status:** plan, 16 November 2026. [ADR-009](../adr/009-model-selection-rule.md) (the
  selection rule) was decided by Martins the same day.
- **Amended 16 November 2026 (Martins):** the feature build and model training run on **Databricks**,
  not on the PC, whose 7.7 GB of memory is not enough (step 2's full build could not start: under
  1 GB free with the usual programs open). The PC stays the reference for code, tests and the
  row-by-row comparison. Everything else in this plan is unchanged: the split, the features, 1-in-25
  sampling with weights in every measure, the COVID sensitivity run, MLflow tracking, `@challenger`
  only. Details: "Where the work runs" below; ADR-001 and L-009 updated.
- **Amended 17 November 2026 (ADR-011, accepted):** the competing-risks hazard is extended to a monthly
  **multi-state (delinquency-transition) model**; steps 3-8 below are replaced by
  [plan_multistate.md](plan_multistate.md) (proposed, pending approval with ADR-009 Amendment 1).
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
73.7 million rows into about 4 million, small enough for one serverless machine on Databricks. The
sampling uses a fixed hash of (loan, month, seed) written so that **DuckDB on the PC and Spark on
Databricks pick exactly the same months** (checked in the row-by-row comparison). The sampling seed
and rate are logged.

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
| 4 | **LightGBM** | Gradient-boosted trees with monotonic constraints on the features marked monotonic above; tuned on the training years only (one inner time split: fit 1999-2013, judge 2014-2016, then refit on all training years) | SHAP values |
| 5 | **Explainable Boosting Machine** (InterpretML) | An additive model of one-feature shapes plus a few pairwise interactions | Its own shape plots |

If the EBM is too slow for the daily allowance on the full training rows, it is trained on a smaller
sample (for example 1 in 100 no-event months instead of 1 in 25, every event kept, weights adjusted
to match), and that choice is documented in its MLflow run, the validation report and the
limitations log. It is still validated and tested on the same weighted validation and test rows as
the other two families, so the comparison under ADR-009 stays like for like.

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

## Where the work runs, and the shared Databricks allowance (amended 16 November 2026)

| Work | Where | Why |
|---|---|---|
| Economic data for the features (silver state and national monthly tables, about 47,000 rows) | **Uploaded once** from the PC to `cecl_lab_dev.silver` | The features need state house prices, unemployment and mortgage rates; until now they lived only on the PC and in Airflow |
| Feature table and sampling | **Databricks** (serverless job, Spark SQL; the same rules as `cecl/features.py`) | The panel and silver are already there (step 9); the PC lacks the memory |
| Row-by-row comparison of the features | **PC** (the DuckDB build, `ingestion/feature_build.py`, on the same vintages) | The PC is the reference: same rows, same values, same weights, as in step 9 |
| Fitting the three families | **Databricks**, **one model family per job** | A run stopped by the daily limit only redoes that family |
| MLflow tracking and the model registry | **Databricks** (the dev experiment and `cecl_lab_dev.ml.pd_hazard`) | Native there |
| Code, unit tests, CI | **PC** and GitHub | Unchanged |

### The order (small slice first)

1. **Upload the economic tables** to `cecl_lab_dev.silver` (and tie them out: rows and values equal
   to the PC's silver files).
2. **Build features for 3 vintages on Databricks** (one early, one crisis, one recent, for example
   2005, 2009 and 2019) and **compare them with the PC row by row**: same rows kept, same feature
   values (to rounding), same outcomes and weights; every tie-out passes on both sides.
3. **Measure the minutes** and **project the full build** (28 vintages). Report the projection; run
   the full build only if it fits the day's allowance with room to spare.
4. **Train one family per job**, starting with the logistic benchmark; measure each before the next.

### Allowance rules (Databricks is shared with Bank Health, L-009)

- **Never on a Monday before 13:00 Chicago, or between 02:30 and 04:00 any day**, unless Martins
  confirms Bank Health's run has finished (as on 16 November).
- **Small slice first**, then the full run only after the projection is reported.
- **One model family per job**; every job has a timeout.
- **Every run's minutes are recorded in the run log** (from the Jobs API, as in step 9), together
  with what it produced.
- If the **EBM** is too slow, it is trained on a smaller, documented sample (section 3–5).
- The full-data vintages planned in ADR-001 (2005–2008, 2019) share the same allowance: Phase 2 runs
  are scheduled so they don't crowd those loads out.

### The PC's part

The PC builds the comparison vintages with DuckDB (small: three vintages need well under 1 GB) and
runs the unit tests. The preflight still guards any heavy local run (`ingestion/ml_preflight.py`:
free memory and disk, dev Airflow stopped), and the PC never trains the full models.

## MLflow tracking

- **Where:** the dev experiment in the Databricks workspace (`/Users/<user>/cecl-lifetime-loss-lab-dev`):
  natively from the training jobs, and from the PC through the CLI profile `cecl` (pinned for every
  Databricks login, so nothing falls back to Bank Health's profile; the token is never read).
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
| 1 | ML environment (`requirements-ml.txt`), MLflow connection from the PC, preflight (memory, disk, Airflow stopped) | A test run appears in the dev experiment (done) |
| 2 | Feature rules (`cecl/features.py`, unit-tested) and the PC's DuckDB build (the reference) | Tested; a trial vintage ties out (done) |
| 2a | Economic tables uploaded; **features for 3 vintages on Databricks**, compared row by row with the PC; minutes measured; full build projected | Same rows and values on both sides; projection reported to Martins |
| 2b | Full feature build on Databricks (after the projection) | Tie-outs and contract pass; minutes in the run log |
| 3–5 | The three families on Databricks, **one family per job**, logged to MLflow | Three runs in MLflow; minutes recorded per job |
| 6 | Validation report and the ADR-009 choice | Report shows every family against the rule |
| 7 | The out-of-time test, once, for the chosen family, the others (for information) and the COVID sensitivity run | Results recorded, whatever they are |
| 8 | Reason codes, registry (`@challenger`) | Reason codes for every loan; alias set |
| 9 | Model documentation draft, inventory entry M-01 updated, limitations log | Phase 2 report with evidence |

## What Martins will be asked for

1. The ADR-009 Decision: done (16 November 2026).
2. Review of the reason-code wording (step 8).
3. Approval before any Databricks compute (first the 3-vintage slice, then the full build after the
   projection), and confirmation of the Monday rule if ever needed.
4. Approval of the Phase 2 report before the model is used in Phase 4.
