# Phase 1, step 8: tie-outs, run log, snapshots, backfill and alerts

- **Status:** built, 16 November 2026. Local runs on the PC; Airflow (dev) for the economic data
  and the health check. Databricks comes in step 9.
- **What:** the controls that prove each run was complete, logged and repeatable: C02 tie-outs
  between the layers, a run-log record for every run (C18), one alert per Airflow run, a
  quarter-end snapshot with a git tag (C07) and a check that a rebuild reproduces it (C08), one
  command for full runs and backfills, and a retention rule for raw downloads.
- **How to run it:** [runbook](../runbook.md).

## The pieces

| Piece | Where | What it does |
|---|---|---|
| Tie-out check | `cecl/controls.py` | Two numbers that must agree exactly (money as exact decimals, to the cent; floats refused). Any mismatch stops the run and lists every failure |
| Pipeline tie-outs (C02) | `ingestion/tie_outs.py` | Per vintage: loans and original balance, bronze vs silver; loan-months and monthly balance, bronze vs silver; loans with monthly rows, silver vs panel; every loan accounted for (panel + no monthly rows yet, L-015); one ending per panel loan. 7 checks × 28 vintages = 196 |
| Run log (C18) | `ingestion/run_log.py` | One JSON record per run in `data/local/ops/run_log/`, written on success *and* failure: rows per layer, contracts, tie-outs, dbt test counts, watch counts (L-013, L-014, L-015, L-018, XX at loan age 0), stale sources, alerts sent, duration, code version |
| Alerts | `ingestion/alerts.py` | Slack and Discord, `[CECL dev]` / `[CECL]`. Every Airflow run ends with exactly one: OK, WARNING (worked, but something to look at) or FAILED (after one automatic retry). Missing or placeholder webhooks are skipped cleanly; laptop and CI runs never post; webhook URLs are never printed |
| Health check | `ingestion/health.py`, DAG `cecl_health_check` | Wednesdays 07:00: weekly pipelines not refreshed in 8 days, latest runs that failed, stale sources |
| One command | `scripts/run_pipeline.py` | Freddie zips → bronze → dbt (silver + tests) → panel → tie-outs → economic data (download, or replay of a saved download). A backfill is the same command for chosen years |
| Snapshot (C07, C08) | `ingestion/snapshot.py`, `scripts/snapshot.py` | Manifest of every input and output file (size, rows, SHA-256) plus the code commit and tree; `verify` compares the files on disk with it; `tag` puts the whole manifest in an annotated git tag |
| Retention | `ingestion/retention.py`, `scripts/retention.py` | Keeps the newest 8 economic downloads per source and every download a snapshot lists; dry run unless `--apply`; never touches Freddie's files |

## Two findings during the build

**1. The first full run failed a tie-out, correctly.** Vintage 2008 came out 1 loan, $145,000 of
original balance and 209 monthly rows short in silver; vintage 2009 the same amount over. No data
was lost: this is L-018 (one loan in the 2008 sample file has a 2009 loan ID; the project's
vintage is the loan-ID year, decided 14 November). The tie-out had grouped bronze by file year
and silver by loan-ID year. Fixed by grouping both by loan-ID year, the documented rule, and
counting such loans as a watch item (`L-018_loans_from_another_years_file`: 1), so the difference
stays visible without being hidden in a tolerance.

**2. Airflow's container health checks piled up and starved the stack.** Under the CPU caps, each
`airflow jobs check` health check took longer than Docker's timeout, and Docker doesn't stop a
timed-out check: dozens accumulated (~100 MB each) until the scheduler hit its 2 GB limit and
Docker could no longer stop the containers (two Docker Desktop restarts). Fixed in
`docker-compose.yaml` (dev and prod): the scheduler uses its own health page (a quick `curl`);
the dag-processor's check is wrapped in `timeout 80` and runs every 2 minutes. Also added: one
automatic retry after 2 minutes for every Airflow task (all are safe to repeat), after a task died
when the API server was slow to start.

**3. A dated exception to the Monday window (dev only).** On Monday 16 November Martins allowed
dev runs before 13:00 to finish these tests. The window is enforced in code, so the exception is
too: `CECL_WINDOW_EXCEPTION=<date>` (`cecl/scheduling.py`) is honoured only when `CECL_ENV=dev`,
only on that Chicago date, and only for the Monday window (never the 02:30–04:00 reboot window);
each task that uses it logs a warning. It is empty unless passed when the dev stack starts, and the
prod stack never honours it. Dev on the PC touches neither the VM nor Databricks.

## Evidence (15–16 November 2026)

**Full local run and reload** (`CECL_ENV=local`, all 28 sample vintages, economic data replayed
from the 15 November download):

| Run | Result | Time | Notes |
|---|---|---|---|
| `20261116T021105Z_freddie_9a9d6c` | failed: TieOutError | 56 min | finding 1 (L-018); run log shows all 196 tie-outs, 8 failing |
| `20261116T030919Z_freddie_3f5ca2` | success | 105 s | tie-outs (fixed) and economic data only: **196 of 196 agree** |
| `20261116T031240Z_freddie_39ec10` | success | 55 min | **full reload**: bronze, dbt (3 models, 17 tests pass), panel, 196 of 196 tie-outs, economic data |
| `20261116T040816Z_freddie_88d5d8` | failed: TieOutError (deliberate) | 12 s | `--simulate-tie-out-failure`: "vintage 2007: loans, bronze vs silver: expected 50001, got 50000"; exit code 1 |
| `20261116T040941Z_freddie_ebb241` | success | 80 s | clean tie-outs after the demonstration |

- **Rows:** bronze 77,417,991 (59 files); panel 1,374,984 loans, 73,711,904 rows (28 files);
  silver economic tables 47,819.
- **Watch counts** (match the limitations log): L-013 363 rows with derived remaining months;
  L-014 215 loans over 360 months; L-015 16 loans without monthly rows; L-018 1 loan from another
  year's file; 34,989 loans whose first month was XX at loan age 0.
- **Reload reproduces the snapshot exactly:** a manifest of all 180 files (89 raw downloads, 59
  bronze, 4 silver, 28 panel) taken after the first run; after the full reload, `verify`:
  **180 matched, 0 changed, 0 missing, 0 extra**. (A rehearsal manifest; the official
  `cecl-2026Q2-data-v1` is created on `main` after this branch is merged, so the tag names
  committed code.)
- **Retention dry run:** keep 3 download folders, remove 0.

**Alerts in dev Airflow** (both channels received each one; outcome recorded in the run log):

| Run | Outcome | Alert |
|---|---|---|
| `cecl_health_check` manual 03:27 UTC | task runner timed out (API server just restarted; before retries were added) | FAILED |
| `cecl_health_check` catch-up of 11 Nov slot | success, 2 problems (no Airflow economic-data run yet; latest run failed) | WARNING |
| `cecl_health_check` manual 03:37 UTC | success, same 2 problems | WARNING |
| `cecl_health_check` manual, `simulate_failure` | failed on try 1 and on the retry | FAILED |
| `cecl_macro_data` manual 13:09 UTC 16 Nov | success: all contracts pass, nothing stale | **OK** |
| `cecl_health_check` manual 13:12 UTC | success, 1 problem (its own previous run was the simulated failure) | WARNING |
| `cecl_health_check` manual 13:13 UTC | success, no problems | **OK** |

The last three ran on Monday morning under Martins's dated exception (below). After the
health-check fix, the scheduler used 245 MB of its 2 GB, with no leftover check processes; both
DAGs were paused again and the stack restarted without the exception.

**Tests:** 320 pass; coverage 100% for every new module (`cecl/controls.py`, `ingestion/alerts.py`,
`health.py`, `retention.py`, `run_log.py`, `snapshot.py`, `tie_outs.py`), 99% overall for
`cecl/` and `ingestion/`; ruff and mypy clean.
