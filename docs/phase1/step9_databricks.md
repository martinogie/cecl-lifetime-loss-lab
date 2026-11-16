# Phase 1, step 9: Databricks dev (`cecl_lab_dev`)

- **Status:** done, 16 November 2026. Closes Phase 1's data foundation on Databricks.
- **What:** the same pipeline as the PC, on Databricks: Freddie's sample zips to bronze Delta
  tables, dbt silver, the ADR-002 panel and the C02 tie-outs, all in the dev catalog
  `cecl_lab_dev`, then a number-for-number comparison with the PC.
- **Done means** (roadmap): dev tables match the local results row for row (counts, tie-outs,
  default rates by vintage), and Databricks minutes are recorded against the quota shared with
  Bank Health. **Both met** (evidence below).

## How it runs

| Part | Where | What |
|---|---|---|
| Bundle | `databricks/databricks.yml` | Schemas `cecl_lab_dev.bronze/silver/gold/ml/ops`, volume `bronze.raw`, MLflow experiment, model `ml.pd_hazard`, jobs `cecl_lab_bronze_load_dev` and `cecl_lab_panel_build_dev`. Serverless; the notebooks install the project wheel (`cecl` + `ingestion` with its data contracts) |
| Upload | Martins's PC → volume (`databricks fs cp`) | The 28 sample zips to `/Volumes/cecl_lab_dev/bronze/raw/freddie/sample/`; never via the VM (ADR-001). Raw loan data stays inside the private workspace |
| Bronze | `notebooks/10_bronze_freddie.py` | `ingestion.freddie.load_zip`, the PC's code: contract check, streamed from the zip, row tie-out; then into Delta per vintage (`replaceWhere`) and tied out again (Delta rows = file rows) |
| Silver | dbt `dev` target, run from the Airflow dev image | The same models and tests as the PC, on the Serverless Starter Warehouse |
| Panel | `notebooks/20_panel.py` | `ingestion.panel_build.loan_panel_rows` (the `cecl.panel` rules) per loan, via Spark `applyInPandas`; `gold.loan_panel` per vintage |
| Tie-outs | `notebooks/30_tie_outs.py` | `ingestion.tie_outs.layer_tie_outs`: the PC's SQL on the Delta tables; results to `ops.run_log` and a JSON file in the volume; any mismatch fails the job |
| Comparison | `scripts/compare_databricks.py` (PC) | Downloads the Databricks result, computes the same numbers locally, lists every difference, records the Databricks minutes in the local run log |

Commands: [runbook](../runbook.md), "Databricks dev".

## Found and fixed

1. **Development mode renamed everything.** The bundle's dev target used Databricks "development
   mode", which prefixed every schema, the volume and the model with `dev_<username>_`. That broke
   the fixed names the code uses and put a username into table names. Development mode was dropped
   (the environment is already in every name); the five empty prefixed schemas and the empty volume
   were deleted and recreated under the right names, with Martins's approval (the deploy tool
   requires explicit confirmation for that).
2. **The dbt sources declared only the origination table.** `bronze.freddie_performance` was
   missing from `_sources.yml` since Phase 0, unnoticed because the local and CI targets never read
   sources. Added.
3. **A typo in `DATABRICKS_HTTP_PATH`** (two extra characters) made dbt's connection fail; found by
   comparing the value's shape (IDs masked) with the warehouse's real path; Martins fixed `.env`.

**Shared workspace.** The Databricks work ran on Monday 16 November from about 08:40 Chicago, inside
the Monday window, after Martins confirmed that Bank Health's weekly run had already finished that
day, so the workspace and the shared compute allowance were free.

## Evidence (16 November 2026)

| Step | Result | Time |
|---|---|---|
| Bundle deploy (`cecl_lab_dev`) | 5 schemas, volume, experiment, model, 2 jobs | — |
| Upload | 28 zips, 1.1 GB | 3 min |
| Bronze trial, vintage 2026 | 25,000 loans, 63,330 monthly rows (= PC) | 1.5 min |
| Bronze, 1999–2026 | 56 files; 1,375,000 loans, 76,002,480 monthly rows (= PC: 77,377,480 rows) | 16.4 min compute |
| dbt build (Databricks) | 3 views, 17 tests: PASS=21, ERROR=0 | 32 s |
| Panel + tie-outs | panel 1,374,984 loans; **196 of 196 tie-outs agree** | 38.2 min compute |
| Comparison with the PC | **0 differences** in every tie-out value, every watch count (L-013 363, L-014 215, L-015 16, L-018 1, XX 34,989), how each vintage's loans ended, and the rows per layer | — |

- **Databricks minutes:** 54.6 minutes of job compute (bronze 16.4, panel and tie-outs 38.2), plus
  about 1.5 for the trial and well under a minute of SQL warehouse time for dbt. Recorded in the run
  log (`databricks_minutes`), against the Free Edition allowance shared with Bank Health (L-009).
- **Run log:** `ops.run_log` on Databricks (the tie-out record) and the local record of the
  comparison (pipeline `databricks`).
- **Tests:** 324 pass locally; ruff and mypy clean.

## What's left for later

- Airflow triggering the Databricks jobs (and dbt) as one DAG comes with the prod set-up; step 9
  ran the three parts by hand, in order.
- Delta table versions in the quarter-end snapshot manifest (C07) are added when the first quarter
  close runs on Databricks.
