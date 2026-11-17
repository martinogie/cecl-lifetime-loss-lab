# Phase 2 build: status, code and evidence

- **Status, 16 November 2026 (night):** steps 1, 2 and 2a done; **all Phase 2 code written and
  tested**; training on Databricks is scheduled for 17 November, after the daily allowance resets
  (Martins's instruction: Bank Health and step 2a used part of the 16 November allowance).
- **Plan:** [plan.md](plan.md) (amended 16 November: features and training on Databricks, the PC as
  the reference). **Rule:** [ADR-009](../adr/009-model-selection-rule.md).

## Step 1: ML environment and MLflow (done)

- `requirements-ml.txt` (pinned: scikit-learn, statsmodels, LightGBM, SHAP, InterpretML, MLflow,
  databricks-sdk, psutil); a separate CI job (`ml`) installs it and runs the model tests.
- `cecl/tracking.py`: the dev experiment and the Unity Catalog registry through the CLI profile
  `cecl`. **Found and fixed:** MLflow's artefact upload fell back to the CLI's *default* profile,
  which is Bank Health's; every Databricks login in a CECL run is now pinned to `cecl` (tested).
  Also fixed: MLflow's emoji output crashed on the Windows console.
- `ingestion/ml_preflight.py`: refuses a heavy local run unless dev Airflow is stopped and enough
  memory and disk are free (it refused the full PC feature build: under 1 GB free).
- Evidence: a `connection-check` run in the dev experiment (two crashed attempts marked FAILED).

## Step 2: features (done) and step 2a: the 3-vintage slice on Databricks (done)

- `cecl/features.py`: the ADR-009 split, the outcome, 18 features (each known at the start of the
  month), contract ranges. `ingestion/feature_build.py`: one SQL for both engines (DuckDB on the PC,
  Spark on Databricks; only five dialect functions differ), the 1-in-25 sample with exact weights,
  the horizon table (every month of 1 loan in 10, validation and test months), tie-outs, contract.
- Databricks: `35_load_macro` (the four silver economic tables into `cecl_lab_dev.silver.macro_*`)
  and `40_features` (per vintage; staging table, tie-outs, then the real tables; test months to the
  holdout tables), job `cecl_lab_features_dev`.
- **Found and fixed on the way:** serverless compute can't `cache()` (staging tables instead);
  Freddie's debt-to-income is missing for 29-35% of the 2010-2013 vintages (relief refinances),
  so DTI's missing limit is 10% (others 5%), recorded as **L-024**; Puerto Rico, Guam and the
  Virgin Islands have no state series (**L-023**). **17 November:** the HARP explanation is
  confirmed (87.4% of missing-DTI loans are relief refinances; every relief refinance has DTI missing),
  and Freddie's relief refinance indicator is now a 19th feature, `relief_refinance` (dbt staging, both
  engines' query, reason wording, tests), so missing DTI doesn't stand in for HARP (L-024).

**Evidence, 2005, 2009 and 2019 (`scripts/compare_features.py`):**

| Check | Result |
|---|---|
| Tie-outs (both sides) | all pass: events and weighted no-event months equal the panel's |
| Training table, PC vs Databricks | **473,160 rows matched; 0 only on one side; 0 columns differing** |
| Horizon table | **161,053 rows matched; identical** |
| Economic tables | rows and value sums identical to the PC's files (4 tables) |
| Current LTV vs Freddie's estimated LTV (2026 trial) | median difference 1.0 point, correlation 0.986 |

**Databricks minutes, 16 November (all in the run log):** step 2a used 7.4 minutes: two failed runs
(1.7 and 2.9 minutes; fixed) and the successful slice (2.8: features 2.1, economic tables 0.7).
**Projection for the full feature build (28 vintages): about 18 minutes** (scaled by panel rows).

## The rest of Phase 2's code (written and tested; runs on 17 November)

| Piece | Where | Tested |
|---|---|---|
| Weighted measures: log loss, AUC, calibration with the ADR-009 bar, PSI, AUC at 12/24/36 months | `cecl/metrics.py` | yes, including "a weighted 1-in-25 sample gives the full table's measures" |
| Scorecard bins, monotone where expected | `cecl/models/binning.py` | yes |
| The three families (logistic benchmark; LightGBM and EBM, one model per cause with its own monotonic constraints) | `cecl/models/families.py` | yes, all three fit, predict and explain |
| ADR-009 selection rule | `cecl/models/selection.py` | yes, every branch |
| Reason codes and the wording table (for Martins's review) | `cecl/models/reasons.py` | yes |
| Train (small time-ordered tuning), evaluate, log to MLflow | `cecl/models/pipeline.py` | yes, including logging to a local MLflow store and reloading the model the way the test notebook does |
| One family per job | `databricks/notebooks/50_train_family.py`, job `cecl_lab_train_family_dev` | deployed, not run |
| The out-of-time test, refusing a second run | `databricks/notebooks/60_out_of_time_test.py`, job `cecl_lab_oot_test_dev` | deployed, not run |
| Selection and validation report; start the test (checks ADR-009, clean commit, blocked windows); register `@challenger` | `scripts/select_model.py`, `scripts/run_oot_test.py`, `scripts/register_challenger.py` | the ADR-009 guard is unit-tested |
| Every run's minutes into the run log | `scripts/record_job_minutes.py` | used for step 2a |

The order for 17 November is in the [runbook](../runbook.md), "Phase 2 on Databricks".
