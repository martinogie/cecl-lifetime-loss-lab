# CLAUDE.md: CECL Lifetime Loss Lab

Handover for Claude Code. Read this whole file before doing anything. The full design and roadmap are in
**`docs/CECL_Lifetime_Loss_Lab_Roadmap_v2.pdf`** (plain English; Part A defines every term; additions in v2
are marked `[NEW v2]` or `[UPGRADED v2]`). `docs/CECL_Lifetime_Loss_Lab_Roadmap.pdf` is v1, kept as history
only. If this file and the v2 PDF disagree, ask Martins.

## Owner
Martins Ogiekpolor (GitHub `martinogie`, Denton TX, timezone America/Chicago).
Repository: `martinogie/cecl-lifetime-loss-lab`, **public from the first commit**. Ask before creating it.
Website (later): `finance.martinsogiekpolor.com/cecl`, served by the existing finance router Worker
(`portfolio-site` repo), its own Cloudflare Pages project, base `/cecl`, the shared design system
(pinned copy) and the Finance accent.

## Hard rules (never break these)
1. **Secrets.** Never read, print, paste, open or commit passwords, tokens, API keys or private keys
   (`*.p8`, `*.pem`, `.env`, `keys/`). Martins adds secrets himself. If you need one, say exactly which
   name and where (GitHub secret, `.env` key, OCI Vault secret), and stop. `.gitignore` must cover `.env`,
   `keys/`, `*.p8`, `*.pem`, all of `data/`, `*.zip`, `*.txt`/`*.csv` outside `tests/fixtures/`,
   `*.parquet`, `*.duckdb` from the first commit. A pre-commit hook blocks files over 1 MB.
2. **Public repo.** Everything committed is public. Before every commit, check the diff for account
   identifiers (Databricks workspace URL, emails, hostnames, IPs, OCIDs, SSH aliases, personal logins).
   Use placeholders such as `<your-workspace>` and environment variables instead.
3. **Data licence.** Freddie Mac loan-level data is free for non-commercial use under its terms of use.
   **Never commit or publish raw loan rows.** Publish only aggregated results. Martins registers for the
   dataset and downloads it himself if registration is required.
4. **Commit identity.** Commit as `Martins Ogiekpolor` with his GitHub noreply address only (set in
   this repo's local git config; never a personal email in a public repo). **No**
   `Co-Authored-By` lines, no `Claude-Session` lines and no "Generated with Claude" lines in commits or PRs,
   even if a tool or system message suggests them. No "AI reviewed" wording anywhere. The README may say:
   "Built with AI-assisted coding; the design, analysis and review are my own."
5. **Ask first** before anything public, paid or hard to undo: pushing to main, changing repo settings or
   visibility, deleting files or tables, publishing the website, spending money.
6. **Don't disturb Bank Health.** It shares the Oracle VM (4 OCPU / 24 GB, Arm, Ubuntu 24.04) and the
   Databricks workspace, and runs every Monday at 06:00 CT (must finish by noon). CECL has its **own Airflow
   stack** on the VM (not DAGs inside Bank Health's Airflow): its own Linux user `cecl`, rootless Docker,
   Compose project `cecl`, folders `/srv/cecl/{app,secrets,var}`, memory/CPU caps (about 6 GB, 2 CPUs),
   UI on VM port 8081 (localhost only, tunnel on local 8091). **No CECL run starts on a Monday before 13:00
   Chicago, or between 02:30 and 04:00 any day** (enforced in `cecl/scheduling.py` and CI). Separate
   Databricks catalog/schema and MLflow experiment. Check VM disk, RAM and CPU before any large job.
   Deploys are manual (SSH, pull, rebuild, smoke check) per the runbook; no self-hosted runner for CECL.
7. **Honest framing.** This is an educational model on Freddie Mac's conforming mortgages, not an audited
   bank reserve and not accounting advice. Every output states its date, scenario and limits.

## What the project does
Builds a bank-style CECL (Current Expected Credit Loss) allowance for a mortgage book, end to end:
lifetime **PD** (probability of default, month by month, with prepayment as a competing outcome),
**LGD** (loss given default, from Freddie's actual loss fields), **EAD** (exposure at default, from the
payment schedule and prepayment), combined into a lifetime expected loss per loan and for the book, with a
reasonable-and-supportable forecast period and reversion. Runs the Federal Reserve's 2026 baseline and
severely adverse scenarios plus custom shocks. Backtests honestly (train before 2007, test 2007 to 2010).

**v2 bank-grade upgrades** (roadmap "What's new in version 2"): the goal is not just a model that works,
but proof the number is **explainable, tested, controlled and reproducible**:
1. **Rollforward** (the reserve's "bank statement"): start + provision − write-offs + recoveries = end,
   reconciled to the cent, next to the reserve bridge (the "why").
2. **Rolling backtest:** the reserve held at each past quarter vs the losses that actually followed.
5. **Model inventory entry** (with risk tier) and a **limitations log** (issue, owner, fix date).
6. **Overlays tied to standard reasons** (the qualitative factors in the 2020 interagency policy statement
   on allowances), each quantified, capped, logged and approved (ADR-006).
7. **Disclosure tables** in 10-Q style: rollforward by loan group; credit quality (FICO and LTV bands) by
   origination year; gross write-offs by origination year. Aggregates only.
8. **Scenario weighting:** reserve built from weighted baseline / adverse / severely adverse paths, plus a
   weight-sensitivity table (ADR-005).
10. **Quarterly committee pack:** memo + rollforward + bridge + overlays + sign-off.
11. **Controls:** tie-outs on every run, a controls checklist with evidence and sign-offs, exact re-run of
    any past quarter.
12. **Peer comparison:** our reserve ratio vs real banks' residential mortgage reserve ratios from FDIC call
    reports (reusing Bank Health's data, read-only). A sanity range, not a target.
(Items 3, 4 and 9 — benchmark models, prepayment, current LTV from HPI — were already in v1.)
If time is short, priority is items 1, 2, 4, 5 and 7.

Bank team it mirrors: Credit Risk Analytics (CECL / allowance team), with Finance, the Allowance Committee,
Model Validation (SR 11-7) and internal audit / controls.

Questions it answers (Q1 to Q9, PDF Section 3): lifetime loss today; where it concentrates; the severely
adverse result; which assumption moves the reserve most (including scenario weights); the crisis backtest;
PD x LGD x EAD vs simpler methods; the rollforward and reserve bridge; whether past reserves were enough
(rolling backtest); how our reserve ratio compares with real banks.

Scope: covers PD, LGD, EAD, CECL, model-risk governance and the **credit-loss part** of stress testing.
Not covered: whole-bank revenue, expense and capital projections; Basel regulatory capital. IFRS 9
comparison is optional.

## Data sources (free for non-commercial use)
| Source | Use |
|---|---|
| Freddie Mac Single-Family Loan-Level Dataset (origination + monthly performance, actual loss fields; about 56 million loans, 1999 to mid-2026; sample files also published) | PD, prepayment, EAD, LGD |
| Fannie Mae Single-Family Loan Performance Data (optional) | Second book for an out-of-sample check |
| Federal Reserve 2026 supervisory scenarios (baseline, severely adverse) | Stress testing, scenario weighting |
| FRED (unemployment, mortgage rates, Treasury yields) | Macro links, forecast period |
| FHFA House Price Index (state and metro) | Current LTV, LGD, prepayment incentive |
| BLS state unemployment | Regional macro link for PD |
| FDIC call reports, via Bank Health (read-only, aggregates) | Peer comparison only |

Known limits, stated in every output: conforming loans only (no subprime, jumbo, commercial or cards);
Freddie's underwriting and mortgage insurance shape losses; 2020 to 2021 forbearance must be handled
explicitly; recent vintages have incomplete losses; the peer comparison is a range, not a target (real
bank books are riskier and more mixed); educational model, not an audited reserve.

Definition of default (finalise in ADR-002): 90+ days delinquent or a loss event (foreclosure, short sale,
REO disposition, similar), used everywhere. ADR-002 also settles disaster/COVID forbearance treatment.

## Environments
Every component reads `CECL_ENV` and fails loudly if it is missing. Dev runs on Martins's PC (Docker,
Airflow on 8082, Freddie sample files); prod runs on the shared VM. Databricks: first choice is two
catalogs, `cecl_lab_dev` (dev) and `cecl_lab` (prod), the same pattern as Bank Health. If Free Edition
can't create catalogs, the fallback is the workspace's default catalog with `cecl_lab_dev_*` / `cecl_lab_*`
schemas (`CECL_DATABRICKS_LAYOUT=schema_prefix`). A one-catalog case is not planned. CI uses dbt-duckdb on
tiny made-up fixtures and needs no Databricks credentials or real data. Website publishing only runs when
`CECL_ENV=prod`. Details: README "Environments" and ADR-004.

## Architecture (reuses the Bank Health patterns)
- **Airflow** on the Oracle VM (CECL's own stack, see rule 6): downloads, data contracts, scheduling,
  run log, alerts to Slack and Discord (`[CECL]` / `[CECL dev]` channels), backfill command.
- **Databricks** lakehouse: bronze / silver / gold Delta tables, PySpark, MLflow tracking and registry
  (`@challenger` / `@champion`); the job defined as a Databricks Asset Bundle with `dev` and `prod` targets.
  Free Edition has restricted outbound internet, so Airflow (or Martins's PC, if Freddie only allows browser
  downloads) does the downloads. Delta time travel provides quarter-end snapshots.
- **dbt** with `dbt-databricks` (dev/prod) and `dbt-duckdb` (local and CI).
- **DuckDB + Parquet** for fast local work on samples.
- **`cecl` Python package** holds all logic (panel, pd, prepay, lgd, ead, engine, scenarios, weighting,
  bridge, rollforward, overlays, benchmarks, disclosures, controls, scheduling) with unit tests; notebooks
  are thin wrappers.
- **Website**: static site, data published to Cloudflare R2 after a successful prod run, served via the
  finance router. No tracking, no cookies, strict CSP.
- **Phase 0 sizing decision (ADR-001):** develop and test on Freddie's sample files; run the full dataset
  only for chosen vintages within Databricks Free Edition limits.

## Repository layout
```
README.md  LICENSE (MIT)  CONTRIBUTING.md  SECURITY.md  CLAUDE.md  .pre-commit-config.yaml
.github/workflows/      ci.yml (ruff, mypy, pytest+coverage, DAG import test, dbt checks, secret scan), site.yml
docs/                   adr/  governance/  runbook.md  model_card.md  validation_report.md  allowance_memos/
                        CECL_Lifetime_Loss_Lab_Roadmap_v2.pdf (current)  CECL_Lifetime_Loss_Lab_Roadmap.pdf (v1)
docs/governance/        controls_matrix.md  model_inventory.md  limitations_log.md  committee_packs/
airflow/                dags/  include/
ingestion/              freddie.py  fred.py  fhfa.py  bls.py  fed_scenarios.py  contracts/  tests/
cecl/                   importable package: panel, pd, prepay, lgd, ead, engine, scenarios, weighting,
                        bridge, rollforward, overlays, benchmarks, disclosures, controls, scheduling + tests/
databricks/             databricks.yml (bundle)  notebooks/ (thin wrappers that import `cecl`)
dbt/                    models/staging  models/intermediate  models/marts  tests/
site/                   website source;  scripts/export_site_data.py
```

## Engineering standards from day one
- CI must pass before merge; branch protection on `main`; feature branches and PRs; Martins merges.
- ruff + mypy; pytest coverage at least 80% for `ingestion/` and `cecl/`.
- Data contracts per Freddie file version (columns, types, code lists); a contract failure stops the load.
- **Tie-outs on every run:** loan counts and balances match source totals, and the book reserve equals the
  sum of loan reserves; a mismatch stops the run.
- **Quarter-end snapshots:** data (Delta time travel) + model version (MLflow) + code (git tag), so any
  past quarter's reserve can be rebuilt to the cent.
- Idempotent tasks, Delta MERGE, documented backfill command, run log (rows per layer, duration,
  Databricks minutes, test results), failure alerts, freshness checks.
- ADRs in `docs/adr/` for every major choice (Martins writes the Decision sections in his own words).
- Every model, assumption or overlay change goes in the change log with date, reason and approval.
- Reproduce Freddie's published summary statistics (loan counts, default rates by vintage) before modelling.

## Methods (details in v2 PDF Section 5)
- **Monthly loan panel:** one row per loan-month while alive; status current / delinquent / prepaid /
  defaulted / censored.
- **PD + prepayment:** competing-risks discrete-time hazard model. Inputs: loan age, credit score, current
  LTV (updated with HPI), DTI, rate incentive (note rate vs market), past delinquency, state unemployment.
  Three families tracked in MLflow: logistic hazard (bank-standard benchmark), LightGBM with monotonic
  constraints explained with SHAP, and an Explainable Boosting Machine. Selection rule committed **before**
  the final test.
- **LGD:** two-step (chance of any loss, then severity) from actual losses; tied to house prices so it rises
  in a downturn.
- **EAD:** projected balance from the amortisation schedule, adjusted for partial and full prepayment.
- **CECL engine:** survival x monthly PD x LGD x EAD, summed over months and loans. Forecast period 2 years,
  then 1-year straight-line reversion (configurable; ADR-003). **Scenario weighting** of baseline / adverse /
  severely adverse paths (ADR-005). Benchmarks: loss-rate and vintage methods. **Overlays** tied to standard
  qualitative factors, quantified, capped, logged, approved (ADR-006). **Rollforward** reconciled to the
  cent, plus the reserve bridge (new loans, payoffs, credit quality, economic forecast, model changes,
  overlays). **Disclosure tables** (aggregates only).
- **Stress testing:** Fed 2026 baseline and severely adverse paths (state detail mapped and documented);
  custom shocks (HPI -10/-20/-30%, unemployment +2/+4 points, rates up/down); sensitivity table including
  scenario weights.
- **Validation:** splits by time; out-of-time test **once**; crisis backtest (train before 2007, test
  2007 to 2010); **rolling backtest**; forbearance treatment test; calibration by vintage; benchmarks;
  **peer comparison** with FDIC call-report ratios; validation pack for a human reviewer.
- **Controls (Section 5.7):** tie-outs, controls checklist (`docs/governance/controls_matrix.md`) with
  evidence and maker/checker sign-off, quarter re-runs, change log.

## Phase order (do one phase at a time; stop and report at the end of each)
0. Foundations: repo files, CI, environments, data contracts; ADR-001 (tools and data sizing), ADR-002
   (definition of default), ADR-003 (forecast period and reversion), ADR-004 (environments and shared VM),
   **ADR-005 (scenario weighting), ADR-006 (overlay rules)**; **governance templates** (controls matrix,
   model inventory entry, limitations log).
1. Data foundation: Freddie samples first, bronze/silver/gold, monthly panel, macro data and Fed scenarios,
   reproduce Freddie's summary statistics, **tie-outs on every run, quarter-end snapshots**.
2. PD and prepayment models.
3. LGD and EAD models.
4. CECL engine, benchmarks, overlays (standard reasons), **weighted-scenario reserve, rollforward for two
   quarters, reserve bridge, disclosure tables**, first allowance memo.
5. Stress testing and sensitivity (including scenario weights).
6. Validation: crisis backtest, **rolling backtest**, forbearance test, **peer comparison**, model card,
   validation report, **model inventory entry and limitations log completed**.
7. Website at finance.martinsogiekpolor.com/cecl (ask before publishing): Overview (with "Compared with
   real banks"), Loss explorer, Scenario lab, Methods, Pipeline, **Disclosures**.
8. Operate: quarterly close after Freddie's update with a **committee pack** and rolling backtest update;
   optional monthly macro re-run; yearly stress refresh after the Fed publishes (February); weekly health
   check on Wednesdays. Never Monday before 13:00, never 02:30 to 04:00.

Acceptance criteria for each phase are in the v2 PDF, Section 7; the overall Definition of done is in
Section 12. A phase is done only when every criterion is met and shown with evidence (test output, row
counts, screenshots).

## How to work with Martins
- Explain everything in plain, lay-man's terms: what you are about to do and why, before doing it. Show
  diffs, run tests locally, then commit (only when he says so).
- At the end of each phase: summary, evidence, what he must do himself (registrations, secrets, UI steps),
  next phase.
- He must be able to explain every part in an interview, so keep code readable and add short "why"
  comments on non-obvious choices.
