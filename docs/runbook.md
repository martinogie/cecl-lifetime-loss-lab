# Runbook

How to run, re-run, check and recover the CECL Lifetime Loss Lab. Commands run from the repository
root with the virtual environment active and `CECL_ENV` set (`local` on the PC). Everything under
`data/` is git-ignored; no command prints loan rows.

Status: Phase 1 (local, dev Airflow and Databricks dev). The VM deploy (prod) is added when it
exists.

## Schedule rules (always)

No CECL run starts on a **Monday before 13:00 Chicago** (Bank Health's weekly run) or **between
02:30 and 04:00 any day** (the VM's reboot window). Airflow tasks and dev/prod commands refuse to
start in those windows (`cecl/scheduling.py`). Check the VM's disk, RAM and CPU before any large job.

**Dated exception (dev only, Martins decides).** To allow dev runs on one Monday morning, start the
dev stack with `CECL_WINDOW_EXCEPTION=<that date>` (PowerShell: `$env:CECL_WINDOW_EXCEPTION='2026-11-16'`
before `docker compose ... up -d`). It works only in dev, only on that date, and never in the
reboot window; tasks log that they used it. Afterwards, restart the stack without it. Prod has no
exception.

## The full local run

```
python -m scripts.run_pipeline --years 1999-2026 --macro replay --macro-date 2026-11-15
```

Steps: Freddie's sample zips -> bronze (contract check and row tie-out per file) -> `dbt build`
(silver views and their tests) -> the ADR-002 panel -> the C02 tie-outs between layers -> the
economic data (`--macro download` fetches fresh files, needs `FRED_API_KEY`; `--macro replay`
rebuilds from a saved download, no key, no internet). Any failure stops the run.

Every run writes one record to `data/local/ops/run_log/<run_id>.json`, success or failure: rows per
layer, contracts, tie-outs, dbt test counts, the watch counts (L-013, L-014, L-015, XX at loan age
0), stale sources, alerts and duration.

## Backfill (re-run part of the data)

The same command with chosen vintages. Every step replaces its output atomically, so a backfill is
safe to repeat:

```
python -m scripts.run_pipeline --years 2007 --macro skip          # one vintage
python -m scripts.run_pipeline --years 2005-2008 --macro skip     # several
python -m scripts.run_pipeline --macro replay --macro-date <date> # economic data from a saved download
```

`--skip-bronze`, `--skip-dbt` and `--skip-panel` skip steps whose output is already right (the
tie-outs always run). In dev Airflow, a past week of economic data is re-run by triggering
`cecl_macro_data` by hand; it downloads afresh, so for an exact past week use the replay command.

## When a tie-out fails

The run stops; nothing downstream uses the data. The run-log record lists every tie-out with
expected and actual values. Typical causes: a partly written bronze folder (re-run the vintage), a
changed dbt filter or join (check the diff), a new Freddie file layout (the contract should have
caught it: update the contract, never the tie-out). Never widen a tie-out to make it pass:
an expected difference gets its own count, as L-015 does.

## Quarter-end snapshot (control C07) and exact re-run (C08)

After the quarter's data is loaded and all tie-outs pass, with all code committed and merged:

```
python -m scripts.snapshot create --quarter 2026Q2      # manifest: every file's size, rows, SHA-256
python -m scripts.snapshot tag --quarter 2026Q2         # annotated tag cecl-2026Q2-data-v1 holding it
git push origin cecl-2026Q2-data-v1                     # Martins approves each push
```

To prove a past quarter can be rebuilt: check out the tag, re-run the full local run (with
`--macro replay --macro-date` set to the download the manifest lists), then

```
python -m scripts.snapshot verify --quarter 2026Q2      # must say: exactly the snapshot
```

A snapshot is never overwritten; a corrected one is `--version 2`. The manifest also lives in the
tag's message, so the record survives the loss of this PC (it holds file names, sizes, row counts
and checksums only, no loan data).

## Raw download retention

```
python -m scripts.retention            # dry run: what would be removed
python -m scripts.retention --apply    # remove it
```

Keeps the newest 8 downloads per economic source and, forever, every download a snapshot lists.
Never touches Freddie's files. Never runs on a schedule: deleting is Martins's call.

## Alerts

**Every Airflow run, scheduled or manual, ends with exactly one alert** to Slack and Discord,
prefixed `[CECL dev]` or `[CECL]`:

- `OK <dag> (run, duration): summary`: every task succeeded (sent by the DAG's last task);
- `WARNING <dag> ...`: the run succeeded but found something to look at (stale data; a health-check
  problem);
- `FAILED <dag>.<task> ...`: a task failed even after its one automatic retry 2 minutes later
  (its `on_failure_callback`; the later tasks don't run).

A failed local pipeline run also sends a FAILED alert (skipped on a laptop, see below).

To test both paths in dev: trigger `cecl_health_check` by hand (an OK or WARNING arrives), then
trigger it with the option `simulate_failure` = true (a FAILED alert arrives; refused outside dev):
`airflow dags trigger cecl_health_check --conf '{"simulate_failure": true}'`. Webhooks: `SLACK_WEBHOOK_URL` and
`DISCORD_WEBHOOK_URL` in each stack's `.env` (Martins sets them). A missing or placeholder webhook
is skipped cleanly. Only the Airflow stacks post; laptop and CI runs never do. The outcome per
channel is in the run log.

## Databricks dev (`cecl_lab_dev`)

Uses the Databricks CLI profile `cecl` (Martins's; the token is never in the repo) and, for dbt,
`DATABRICKS_HOST`, `DATABRICKS_TOKEN` and `DATABRICKS_HTTP_PATH` from `.env` in the Airflow
containers. Shared workspace: never on a Monday before 13:00 Chicago unless Martins confirms Bank
Health's run has finished; check the compute allowance first (L-009).

```
cd databricks
databricks bundle deploy -t dev --profile cecl                       # schemas, volume, jobs, wheel
cd ..
databricks fs cp data/raw/freddie/sample/sample_2007.zip   dbfs:/Volumes/cecl_lab_dev/bronze/raw/freddie/sample/sample_2007.zip --profile cecl   # each zip
cd databricks
databricks bundle run bronze_load -t dev --profile cecl --params years=1999-2026     # ~16 min
cd ..
docker exec -w /opt/airflow/dbt cecl-dev-airflow-scheduler-1 sh -c '"$DBT_EXECUTABLE" build --profiles-dir .'
cd databricks
databricks bundle run panel_build -t dev --profile cecl --params years=1999-2026     # ~38 min
cd ..
python -m scripts.compare_databricks --years 1999-2026     # must say: 0 differences
```

(In Git Bash, prefix the `docker exec -w /opt/...` line with `MSYS_NO_PATHCONV=1`.) A backfill is
the same with fewer years: each notebook replaces only the vintages it is given. The comparison
writes a run-log record with the Databricks minutes.

## Model work on the PC (Phase 2)

Since 16 November 2026 the full feature build and model training run on **Databricks** (Phase 2
plan, amended): the PC's 7.7 GB of memory is not enough. The PC builds the comparison vintages with
DuckDB, runs the tests, and talks to MLflow. Install once:
`pip install -r requirements-dev.txt -r requirements-ml.txt` (about 1.5 GB). Commands that use MLflow
run as `CECL_ENV=dev` through the CLI profile `cecl` (`cecl/tracking.py` pins every Databricks login
in the run to that profile, so nothing falls back to the CLI's default profile, which is Bank
Health's).

**Before any heavy local run** (the PC has 2 cores and 7.7 GB of memory):
1. Stop the dev Airflow stack (`cd airflow; docker compose -f docker-compose.yaml -f docker-compose.dev.yaml down`).
2. **Quit Docker Desktop** (tray icon → Quit). Its background Linux machine keeps 2–3 GB of memory
   even with no containers running, and training doesn't need Docker.
3. Close what you can (browsers, Slack, Discord).
4. Every training command runs the preflight first and refuses to start unless at least 4 GB of
   memory and 5 GB of disk are free and no dev Airflow container is running
   (`ingestion/ml_preflight.py`); the figures and the run's peak memory are logged.

Check the connection (no training): `CECL_ENV=dev python -m scripts.mlflow_check` logs a small
`connection-check` run to the dev experiment.

## Phase 2 on Databricks: features and training

Allowance rules (L-009): never on a Monday before 13:00 Chicago or between 02:30 and 04:00; one model
family per job; record every run's minutes (`python -m scripts.record_job_minutes --job <name>`).
Run from `databricks/` with the project's environment first on the PATH (the bundle builds the
wheel): `PATH="../.venv/Scripts:$PATH" databricks bundle ... --profile cecl`. Job parameters are
comma-separated pairs, so a value can't contain a comma (use ranges such as `1999-2026`).

0. **Refresh dbt's silver views on Databricks** (seconds; needed after any staging change, such as
   the relief refinance flag added on 17 November): start Docker Desktop and the dev Airflow stack, then
   `docker exec -w /opt/airflow/dbt cecl-dev-airflow-scheduler-1 sh -c '"$DBT_EXECUTABLE" build --profiles-dir .'`
   (Git Bash: prefix `MSYS_NO_PATHCONV=1`); stop the stack again before heavy local work.
1. **Full feature build** (about 18 minutes projected):
   `databricks bundle run features -t dev --profile cecl --params years=1999-2026,export_slice=false`
2. **Train one family per run**, logistic first (`<sha>` = `git rev-parse HEAD`):
   `databricks bundle run train_family -t dev --profile cecl --params family=logistic,code_version=<sha>`
   then `family=lightgbm`, then `family=ebm` (if too slow for the allowance: add
   `ebm_sample_every=4`, which is recorded in the run and must be noted in the report and L-log).
3. **Choose** (PC, no compute): `CECL_ENV=dev python -m scripts.select_model` writes
   `docs/phase2/validation_report.md` and a `selection` run.
4. **COVID sensitivity run** of the chosen family:
   `... train_family ... --params family=<chosen>,include_covid=true,code_version=<sha>`
5. **The out-of-time test, once** (commit everything first):
   `CECL_ENV=dev python -m scripts.run_oot_test`
6. **Register `@challenger`**: `CECL_ENV=dev python -m scripts.register_challenger`

After each Databricks run: `CECL_ENV=local python -m scripts.record_job_minutes --job <job name>`.

### The multi-state design (ADR-011; plan: docs/phase2/plan_multistate.md)

Steps 2-6 above are replaced by these (the old single-model jobs stay for reproducibility). Every
command is started from a clean commit (`scripts/run_job.py` refuses otherwise, deploys from that
commit and records the minutes); no daily cap, but the blocked windows always apply.

1. **Slice, then compare with the PC** (M2):
   `CECL_ENV=local python -m scripts.run_job --job cecl_lab_transition_features_dev --param years=2005,2009,2019 --param export_slice=true`
   then `CECL_ENV=local python -m scripts.compare_transitions --years 2005,2009,2019` (must show 0 differences).
2. **Full build** (M3): `... --job cecl_lab_transition_features_dev --param years=1999-2026 --param export_slice=false`.
3. **The 9 models, back to back** (M4), shortest first; waits out blocked windows, stops at the first
   failure: `CECL_ENV=local python -m scripts.run_sequence --plan transitions` (resume: `--from <n>`).
4. **Projection check, 2017-2019** (M5), after selection:
   `... --job cecl_lab_projection_check_dev --param 'combinations={"chosen": {...}, "all_logistic": {...}}'`.
5. The out-of-time test only after Martins approves it (ADR-009 E).

## Dev Airflow on the PC

```
cd airflow
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d     # start (UI: localhost:8082)
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml down      # stop
```

DAGs start paused. `cecl_health_check` (Wednesdays 07:00) reads the run log and alerts if the
economic data hasn't refreshed in 8 days, the latest run of a pipeline failed, or a source is
stale. `cecl_macro_data` (Wednesdays 08:00) loads the economic data and writes its run-log record.

## FFIEC call reports (Phase 6 peer comparison)

Source: the FFIEC Central Data Repository Public Web Service (REST API, SIS611 v1.10), Schedule RI-C
for every institution with $1bn or more in assets. Code: `ingestion/ffiec.py`, contract
`ingestion/contracts/ffiec_call.yml`, Airflow DAG `cecl_ffiec_call_reports` (quarterly, 15th of
February, May, August and November at 14:00 Chicago). Bank Health is never touched.

1. **Secrets (Martins):** create the PWS account at cdr.ffiec.gov, generate a token, and add
   `FFIEC_PWS_USERNAME` and `FFIEC_PWS_TOKEN` to `.env` on the PC and in the CECL stack's `.env` on the VM
   (`/srv/cecl/secrets`). Never in git, never printed.
2. **Initial load (PC):** `CECL_ENV=local python -m scripts.load_ffiec`. It covers year-ends 2020-2025,
   30 June 2026 and the latest public quarter: about 1,100 banks per period, one call every 2 seconds, so
   about 4-5 hours. It is resumable: stop and re-run at any time, and finished bank-periods are skipped
   (cache in `data/raw/ffiec/`). It refuses to start on a Monday before 13:00 or between 02:30 and 04:00.
3. **Output:** aggregates only, in `docs/phase6/peer_ratios.json`: the median, middle 50% and 10th-90th
   percentile of the residential allowance ratio by bank size, and the pooled ratio.
4. **Token renewal (every 90 days):** the Wednesday health check warns in the [CECL] channels from 14 days
   before expiry. Loads stop with "The FFIEC PWS token expired ..." once it has expired. Generate a new
   token in the PWS account and replace `FFIEC_PWS_TOKEN` in both `.env` files.
5. **If the contract fails** (for example RI-C missing for more than 5% of filers, or RI-C item 6 not
   equal to Schedule RC item 4.c for more than 2%), the load stops before bronze. Look at the cached
   `status` values in `data/raw/ffiec/<period>/` and fix the contract or the parser. Never loosen a check
   just to make it pass.

## Website data refresh (automatic, after each successful prod run)

The site's numbers come from `/cecl/data/*.json`. After every successful prod run the `cecl_publish_site`
DAG (triggered by the quarterly close, or by hand) builds the six files (`ingestion/site_export.py`), checks
them against `site/data/schemas/`, scans them for loan ids and identifiers, uploads them to a new run folder
in the **private** R2 bucket `cecl-data`, checks every object, and switches `current.json`. The finance router
reads that pointer, so the site refreshes **without a rebuild or deploy**. Until the first publish (or if a
file is ever missing) the router serves the copy built into the Pages site.

**What a publish shows** (`site_export.build`): the committed Phase 4 closes (31 December 2025 to 30 June 2026)
plus every live close the VM has fetched into `data/close/<R>/close.json` whose controls all passed. The newest
one is the headline and the reserve date on every page; each live close adds its quarter's rollforward and
bridge; its disclosures and Loss-explorer groups (saved in the close record as aggregates) replace the older
ones. The pages read the dates and quarters from the data, so a new quarter needs no page change.

- **One-off set-up (Martins):** create the R2 bucket `cecl-data` (Cloudflare → R2 → Create bucket); create an
  R2 API token with **Object Read & Write on `cecl-data` only**; put `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
  and `R2_ENDPOINT` (`https://<account id>.r2.cloudflarestorage.com`) in the CECL stack's secrets on the VM
  (`/srv/cecl/secrets`) and, for dev runs, in `.env`. Then merge the router change in portfolio-site and run its
  Deploy (target `worker`), which adds the `CECL_DATA` binding.
- **Prod only:** `CECL_ENV=prod` publishes at the bucket's root (what the site serves); dev writes under
  `dev/` (never served); local never publishes.
- **Rollback:** `python -m ingestion.site_publish list`, then `python -m ingestion.site_publish rollback <run key>`.
  The newest 5 runs are kept.
- **Code changes** to the pages still go through a PR and the manual **Deploy site** workflow; data does not.

## Quarterly close (Phase 8, ADR-012)

Once per environment, before the first live close (PC, `CECL_ENV=prod`): seed the Phase 4 closes the live
close starts from, after a dry run that checks every booked frame against the committed totals:

```sh
CECL_ENV=prod python -m scripts.seed_close          # dry run: builds and checks, uploads nothing
CECL_ENV=prod python -m scripts.seed_close --yes    # uploads results/close/<ym>/ and models/phase3/
```

Each quarter, after Freddie's release (never Monday before 13:00 or with 02:30 within 4 hours):

1. **Freddie files** (Martins, ADR-001): download the new sample zips in the browser and upload them to
   `/Volumes/cecl_lab/bronze/raw/freddie/sample/` (one `sample_<year>.zip` per origination year). If Freddie
   changed the file layout, add the release's data contract first (a dev step with its own PR).
2. **Overlay review** (Martins, ADR-006): add this quarter-end's review to `docs/governance/overlay_log.json`
   (an overlay or an assessment with `reserve_ym`), maker and checker, merged to main.
3. **Deploy** from main: on the VM pull and restart (as in `docs/vm_cutover.md`); from the PC
   `databricks bundle deploy -t prod --profile cecl` at the same commit (the close refuses jobs deployed from
   another commit).
4. **Trigger** `cecl_quarterly_close` in the VM's Airflow with `reserve_ym` (e.g. `202609`) and Freddie's release
   number. It takes about 2.5 to 3 hours; a failed step alerts and stops (heavy jobs never retry by themselves).
5. **Website** (automatic, ADR-012 decision): once every control has passed, the close triggers
   `cecl_publish_site`; the site shows the new quarter within a minute (newest date, its rollforward and bridge,
   disclosures and Loss explorer). Keep `cecl_publish_site` unpaused. A failed close publishes nothing.
6. **Review**: the VM holds `data/close/<R>/close.json`, `monitoring.json` and `committee_pack.md`. They go into
   `docs/governance/committee_packs/<YYYYQn>/` through a PR; the maker and checker sign on different days;
   the merge commit is tagged `cecl-<YYYYQn>-close`.

A failed control leaves `results/close/<R>/controls_failed.json` in the volume and nothing booked. Fix the
cause and trigger again (every step is safe to repeat). Re-running a *past* quarter is not done by this DAG:
rebuild it from its snapshot (the close record's Delta versions, model versions and commit).

**Reminder:** `cecl_quarterly_reminder` posts to the `[CECL]` Slack and Discord channels at 14:00 Chicago on
the 15th of January, April, July and October, with the quarter-end to close and these steps. It only reminds;
it never checks Freddie's site or downloads anything (ADR-001). Keep it unpaused.

## Model monitoring (Phase 8, ADR-012)

`cecl_weekly_monitoring` runs on Wednesdays at 09:00. It reports each source's freshness from what the VM holds.
When a new economic month has arrived, it scores the live book on Databricks and compares each transition's
scores with the baseline by PSI: amber above 0.10, red above 0.25. The quarterly calibration runs inside the
close. Retraining is due yearly, or when two quarters in a row show a judged default cell outside 0.80-1.25 or
the same transition with PSI above 0.25 (`cecl.monitoring.retrain_due`); a retrain is a new model version with
its own validation and Martins's approval.
