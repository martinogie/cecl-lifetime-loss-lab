# Runbook

How to run, re-run, check and recover the CECL Lifetime Loss Lab. Commands run from the repository
root with the virtual environment active and `CECL_ENV` set (`local` on the PC). Everything under
`data/` is git-ignored; no command prints loan rows.

Status: Phase 1 (local and dev Airflow). The Databricks steps (Phase 1 step 9) and the VM deploy
(prod) are added when they exist.

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

## Dev Airflow on the PC

```
cd airflow
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d     # start (UI: localhost:8082)
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml down      # stop
```

DAGs start paused. `cecl_health_check` (Wednesdays 07:00) reads the run log and alerts if the
economic data hasn't refreshed in 8 days, the latest run of a pipeline failed, or a source is
stale. `cecl_macro_data` (Wednesdays 08:00) loads the economic data and writes its run-log record.
