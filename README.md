# CECL Lifetime Loss Lab

How much will a mortgage book lose over its whole life, and how much worse in a severe recession?
This project builds a bank-style **CECL allowance** (the loan-loss reserve US banks must hold) for
real US mortgages, end to end: lifetime **PD** (chance of default, month by month, with prepayment
as a competing outcome), **LGD** (share lost when a loan defaults) and **EAD** (amount owed at
default), combined into a lifetime expected loss per loan and for the whole book, then re-run under
the Federal Reserve's 2026 stress scenarios.

> **Educational model, not an audited bank reserve and not accounting advice.** It uses Freddie
> Mac's conforming mortgages only. Every output states its date, scenario and limits.

**Status (November 2026): Phase 0, foundations.** The repository skeleton, checks, data contracts
and environment set-up exist. No data has been loaded and no model has been built yet.

## Questions it will answer

| # | Question | Phase |
|---|---|---|
| Q1 | What is the lifetime expected loss of the book today, in dollars and as a % of balances? | 4 |
| Q2 | Which loans and vintages carry most of it (credit score, LTV, debt-to-income, state, vintage)? | 4 |
| Q3 | How much would losses and the reserve rise under the Fed's 2026 severely adverse scenario? | 5 |
| Q4 | Which assumption moves the reserve most: house prices, unemployment, prepayment speed, or the forecast period? | 5 |
| Q5 | Trained only on data before 2007, would the model have predicted the 2007 to 2010 losses? | 6 |
| Q6 | How does PD × LGD × EAD compare with the simpler loss-rate and vintage methods? | 4 |
| Q7 | What changed in the reserve from one quarter to the next, and why? | 4, 8 |

## Data

| Source | Use |
|---|---|
| Freddie Mac Single-Family Loan-Level Dataset (origination and monthly performance files, actual loss fields) | PD, prepayment, EAD, LGD |
| Federal Reserve 2026 supervisory scenarios (baseline, severely adverse) | Stress testing |
| FRED (unemployment, mortgage rates, Treasury yields) | Economic links, forecast period |
| FHFA House Price Index (state and metro) | Current LTV, LGD, prepayment incentive |
| BLS state unemployment | Regional economic link for PD |

**Data licence.** Freddie Mac's data is free for non-commercial use under its terms of use. This
repository **never contains loan-level rows**: all data stays in git-ignored folders and in the
lakehouse, and only aggregated results are published. The test fixtures are synthetic.

**Known limits** (stated in every output): conforming loans only (no subprime, jumbo, commercial or
cards); Freddie's underwriting and mortgage insurance shape the losses; 2020 to 2021 forbearance is
handled explicitly; recent vintages have incomplete losses.

## Architecture

| Layer | Tool |
|---|---|
| Orchestration | Airflow (its own instances; separate from the Bank Health project's) |
| Storage and processing | Databricks lakehouse (bronze / silver / gold Delta tables, PySpark); DuckDB for local work on Freddie's sample files |
| Transformations | dbt (`dbt-databricks` for dev and prod, `dbt-duckdb` locally) |
| Modelling | The `cecl` Python package, with unit tests; MLflow tracking and model registry |
| Quality | Data contracts per Freddie file layout, CI (ruff, mypy, pytest with coverage, DAG import check, dbt build, gitleaks) |
| Website (Phase 7) | Static site at finance.martinsogiekpolor.com/cecl; data published only by prod |

```
cecl/          the package: all modelling logic (+ tests)
ingestion/     downloads, data contracts (contracts/), loaders (+ tests, synthetic fixtures)
airflow/       DAGs, Docker image, Compose files for dev and prod
databricks/    Databricks Asset Bundle (databricks.yml) and thin notebooks that import cecl
dbt/           staging / intermediate / marts models, tests, synthetic seed fixtures
site/          website source (Phase 7);  scripts/export_site_data.py
docs/          ADRs, runbook, model card, validation report, allowance memos, roadmap
```

## Environments

Every component reads `CECL_ENV`. It has no default: if it is missing or wrong, DAGs fail to load,
Airflow containers refuse to start, and dbt, the notebooks and the scripts stop
([cecl/environment.py](cecl/environment.py)).

| | local | ci | dev | prod |
|---|---|---|---|---|
| Runs | a laptop | GitHub Actions | Docker on the PC, own Compose project `cecl-dev` and metadata database | the Oracle Cloud VM shared with Bank Health, as its own Compose project `cecl` (own containers, network, volumes, metadata database), under the role user `cecl` with rootless Docker and `/srv/cecl/`; deployed by hand (SSH, pull, rebuild, smoke check) |
| Airflow UI | none | none | `localhost:8082` | VM port 8081 (localhost only), via SSH tunnel on local 8091 |
| Resource caps | none | none | same as prod | about 6 GB RAM and 2 CPUs in total, so Bank Health is never starved |
| Alerts | none | none | own channels, prefixed `[CECL dev]` | own channels, prefixed `[CECL]` |
| Databricks | none | none | catalog `cecl_lab_dev` | catalog `cecl_lab` |
| Fallback if a new catalog is not allowed | | | schemas `workspace.cecl_lab_dev_*` | schemas `workspace.cecl_lab_*` |
| dbt target | `local` (DuckDB) | `local` (DuckDB, synthetic fixtures) | `dev` | `prod` |
| MLflow experiment | none | none | `cecl-lifetime-loss-lab-dev` | `cecl-lifetime-loss-lab` |
| Model aliases | none | none | may set `@challenger` | sets `@challenger`; moves `@champion` after review |
| Writes prod, publishes the site | no | no | no | yes |

Connection values (workspace host, warehouse path, token) come from environment variables only,
never from files in this repository. Catalog names are fixed in code, so a wrong `.env` cannot
point dev at prod.

**Shared VM.** Prod shares an Oracle Cloud Arm VM (4 OCPU, 24 GB RAM) and a Databricks workspace
with another live project, Bank Health, whose weekly run starts Mondays at 06:00 Central and must
finish by noon. CECL must never change, restart or slow it. The two share the machine, never an
Airflow instance, Linux user, folders, ports, catalogs or MLflow experiments. Nothing is opened to
the internet. The VM keeps only CECL's code, logs and small outputs; Freddie files go to Databricks
storage. Design and trade-offs:
[ADR-004](docs/adr/004-environments-and-shared-vm.md).

**Blocked windows** (Chicago time): no CECL run starts on a **Monday before 13:00** or between
**02:30 and 04:00 on any day** (the VM's automatic reboot). CI rejects any DAG schedule that can
fire in those windows, and every task refuses to start in them even when triggered by hand
([cecl/scheduling.py](cecl/scheduling.py)). The weekly health check runs on Wednesdays.

## How changes are tested

Every change goes through a branch and a pull request; `main` moves only when CI passes.

| Check | What it does |
|---|---|
| `lint` | `ruff check` and `ruff format --check` |
| `types` | `mypy` on `cecl/`, `ingestion/`, `airflow/dags/`, `scripts/` |
| `unit` | all unit tests; fails under 80% coverage for `cecl/` or for `ingestion/` |
| `dag-import` | builds the Airflow image and loads every DAG as dev and as prod; a missing `CECL_ENV` must be refused; no schedule may fire in a blocked window |
| `docker (linux/amd64)`, `docker (linux/arm64)` | builds the Airflow image for both platforms (arm64 is the shared VM, so every package must have an Arm build); the image must refuse to start without `CECL_ENV`. The arm64 build uses GitHub's Arm runners, which are free only for public repos, so it runs only when the repo is public |
| `dbt` | `dbt build` on DuckDB with synthetic fixtures; dbt must refuse to run without `CECL_ENV` |
| `secrets` | no data file is ever tracked and nothing is over 1 MB; gitleaks over the whole git history and the current files |

CI never needs real Freddie Mac data, Databricks access or any secret. Dependabot proposes updates
to the GitHub Actions weekly ([.github/dependabot.yml](.github/dependabot.yml)).

*Later:* pin each third-party action to a full commit SHA (with the version as a comment) instead
of a tag like `@v7`, so a moved or compromised tag can't change what CI runs. Dependabot can keep
SHA pins up to date.

Locally, pre-commit runs ruff, a 1 MB file-size limit, a private-key check, gitleaks and the
repository hygiene test (no emails, workspace hosts, IP addresses or OCIDs in any file).

## Running it locally

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: Python 3.11, a virtual environment,
`pip install -r requirements-dev.txt -r requirements-lint.txt`, then `pytest`. Airflow is not
installed on Windows; its checks run in CI and in the dev Docker stack.

## Credits

Built with AI-assisted coding; the design, analysis and review are my own.

Licence: [MIT](LICENSE) for the code and documentation. Source data keeps its own terms of use.
