# Contributing

A one-person portfolio project. These are the working rules, written down so every change follows
them.

## Workflow

1. Branch from `main` (`feature/<short-name>`). Nothing is pushed straight to `main`.
2. Run the checks locally (below). Fix everything before pushing.
3. Open a pull request. CI must pass: `lint`, `types`, `unit`, `dag-import`, `dbt`, `secrets`.
4. Martins reviews and merges.

Before every commit, check the diff for account identifiers: workspace URLs, emails, hostnames,
IP addresses, OCIDs. Use placeholders (`<your-workspace>`) and environment variables instead. The
pre-commit hygiene hook and CI check this too.

## Commits

- Author: `Martins Ogiekpolor` with his GitHub noreply address, set in this repository's local git
  config. Never a personal email.
- No `Co-Authored-By` lines and no "Generated with" lines in commits or pull requests.
- Messages say what changed and why, in plain words.

## Local set-up (Windows, PowerShell)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -r requirements-lint.txt
pip install -e .
# dbt: see the Windows note at the top of requirements-dbt.txt if this fails on a certificate
pip install -r requirements-dbt.txt
New-Item -ItemType Directory -Force data\local   # where the local DuckDB file lives (git-ignored)
pre-commit install
Copy-Item .env.example .env      # then fill in values; .env is git-ignored
```

Python 3.11 matches Databricks serverless (environment 2). The Airflow image uses Python 3.12.

**Airflow is not installed on Windows.** The DAG import check runs in CI, inside the Airflow image.
To run Airflow, use the dev Docker stack (below).

## Checks

```powershell
$env:CECL_ENV = "local"
ruff check . ; ruff format --check .
mypy
pytest --cov
coverage report --include="cecl/*" --fail-under=80
coverage report --include="ingestion/*" --fail-under=80
$env:CECL_DUCKDB_PATH = "../data/local/cecl_lab.duckdb" ; $env:DBT_SEND_ANONYMOUS_USAGE_STATS = "false"
cd dbt ; dbt build --profiles-dir . ; cd ..
pre-commit run --all-files
```

## Environments

`CECL_ENV` is required everywhere (`local`, `ci`, `dev`, `prod`); see the README's
"Environments" section. Rules enforced in code ([cecl/environment.py](cecl/environment.py)):

- only `prod` writes prod objects, publishes the site, or moves a model's `@champion` alias;
- Airflow stacks run only as `dev` or `prod`, and every task checks `CECL_ENV` before it starts;
- catalog names are fixed in code; connection values come from environment variables.

### Dev Airflow (Docker on the PC)

```powershell
cd airflow
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml build
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up airflow-init
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d
# UI: http://localhost:8082
```

Dev has its own Compose project (`cecl-dev`), so its runs and metadata never mix with prod's.

## Schedules

Prod shares a VM with Bank Health. No run may start on a Monday before 13:00 Chicago (Bank Health's
weekly run) or between 02:30 and 04:00 on any day (the VM's automatic reboot). CI tests every DAG's
schedule; each task checks the clock before it starts (`cecl/scheduling.py`).

## Data

Never commit data. `.gitignore` blocks `data/`, `*.txt`, `*.csv`, `*.zip`, `*.parquet` and
`*.duckdb` (except small synthetic fixtures under `tests/fixtures/`), and pre-commit blocks any file
over 1 MB. Fixtures must be made up, never copied from real files.
