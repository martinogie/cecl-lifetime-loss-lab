# Security

A one-person portfolio project on public data. What needs protecting is the ability to run,
change or spend on the infrastructure, and Freddie Mac's loan-level data, which must never be
redistributed.

**Reporting a problem:** open a GitHub issue *without* any secret or data in it, or contact the
owner through github.com/martinogie.

## What never goes in this repository

- **Secrets:** passwords, tokens, API keys, private keys (`.env`, `keys/`, `*.p8`, `*.pem`). They
  live in a git-ignored `.env`, in GitHub Actions secrets, or in a secrets vault, and are added by
  the owner only.
- **Account identifiers:** workspace URLs, email addresses, hostnames, IP addresses, cloud resource
  IDs. Placeholders (`<your-workspace>`) and environment variables are used instead.
- **Loan-level data:** no raw or row-level Freddie Mac data, ever. Only aggregated results are
  published.

## How that is enforced

| Layer | Check |
|---|---|
| `.gitignore` | blocks secrets files and every data format (`data/`, `*.txt`, `*.csv`, `*.zip`, `*.parquet`, `*.duckdb`), except synthetic fixtures |
| pre-commit | gitleaks, private-key detection, a 1 MB file-size limit, the repository hygiene test |
| CI | gitleaks over the full history and the current files; the hygiene test |
| GitHub (once public) | secret scanning and push protection, turned on by the owner |
| Code | `cecl/environment.py`: dev, ci and local cannot write prod objects, publish, or move `@champion` |

## Credentials (planned; names only)

| Credential | Where it will live | Used by | Phase |
|---|---|---|---|
| Databricks token for dev | dev `.env` on the PC | dev Airflow, dbt `dev` | 1 |
| Databricks token for prod | CECL's own secrets folder on the shared Oracle VM, separate from Bank Health's | prod Airflow, dbt `prod` | 1 |
| Slack and Discord webhooks | `.env` per environment | failure and success alerts | 1 |
| Cloudflare R2 write key | prod only | site data publish | 7 |

Whether Databricks Free Edition allows separate dev and prod credentials is an open question for
ADR-004; if it does not, that limitation is recorded there.

## If something leaks

1. Revoke the credential at its source first (Databricks, Slack, Cloudflare).
2. Create a new one and update where it lives.
3. Remove it from git history if it was ever committed, and record what happened in the runbook.
