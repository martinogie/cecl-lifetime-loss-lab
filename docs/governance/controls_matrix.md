# Controls matrix

Every check that proves the reserve wasn't produced by mistake: what it proves, how often it runs,
the evidence kept, and who signs it off. Mirrors what internal audit / SOX testing expects from a
bank's allowance process (roadmap v2, section 5.7).

**Maker / checker.** This is a one-person project, so Martins is both: he prepares (maker) and,
separately and in writing, reviews (checker). The two sign-offs are recorded on different days where
possible, so the review is a real second look.

**Status key:** *built* (exists and runs), *planned* (phase shown), *n/a* (not applicable, with reason).

## Controls

| ID | Control | What it proves | Type | Frequency | Evidence kept | Where | Phase | Status |
|---|---|---|---|---|---|---|---|---|
| C01 | Data contract check on every Freddie file | Files have the expected columns, types and codes; a broken file never reaches bronze | Automated, preventive | Every load | Contract result per file (rows, errors, warnings) in the run log | `ingestion/data_contracts.py`, `ingestion/contracts/` | 0 | built |
| C02 | Tie-out: loan counts and balances vs source file totals | Nothing lost or double counted between raw files and the panel. Recent loans with no monthly rows yet are allowed and reported, not a mismatch (L-015) | Automated, detective; a mismatch stops the run | Every run | Tie-out table in the run log, including the counts of loans with no monthly rows, empty remaining-months rows (L-013) and terms over 360 months (L-014) | `cecl.controls` | 1 | planned |
| C03 | Reproduction of Freddie's published summary statistics | Loan counts and default rates by vintage match Freddie's own figures (within stated tolerance) | Automated, detective | Each new data release | Comparison table with differences explained | Phase 1 notebook + test | 1 | planned |
| C04 | Tie-out: book reserve = sum of loan reserves | The aggregation adds up exactly | Automated, detective; stops the run | Every engine run | Tie-out result in the run log | `cecl.controls` | 4 | planned |
| C05 | Rollforward reconciles to the cent: start + provision − write-offs + recoveries = end | The reserve's movement is fully explained | Automated, detective | Every quarter | Rollforward table with the reconciliation line | `cecl.rollforward` | 4 | planned |
| C06 | Reserve bridge totals equal the rollforward's change | The "why" adds up to the "what" | Automated, detective | Every quarter | Bridge and rollforward side by side | `cecl.bridge` | 4 | planned |
| C07 | Quarter-end snapshot: data version (Delta), model version (MLflow), code version (git tag) | Any past quarter can be rebuilt | Automated, preventive | Every quarter end | Snapshot record with the three versions | Phase 1 job | 1 | planned |
| C08 | Exact re-run of a past quarter | The snapshot really reproduces the reserve to the cent | Manual, detective | At least once a year, and before the Definition of done | Re-run output and difference (must be zero) | runbook | 6 | planned |
| C09 | Change log entry for every model, assumption or overlay change | No undocumented change to the reserve | Manual, preventive | Every change | Change log entry: date, reason, approval | change log | 0 onwards | planned |
| C10 | Overlay rules check (ADR-006): reason, sizing, cap, expiry, maker/checker | Overlays follow the rules and can't hide model problems | Manual, detective | Every quarter | Overlay log in the committee pack | ADR-006, committee pack | 4 | planned |
| C11 | Scenario weights documented and approved (ADR-005) | Weights aren't changed to steer the number | Manual, preventive | Every quarter | Weights, reasons and sensitivity table in the memo | ADR-005 | 4 | planned |
| C12 | Model promotion gate: a challenger becomes champion only after a written review, and only in prod | No untested model reaches the reserve | Automated + manual, preventive | Every promotion | Review note; MLflow alias history | `cecl/environment.py` (alias guard), promotion runbook | 2 | partly built (guard) |
| C13 | Environment separation: dev, ci and local can't write prod, publish or move `@champion` | Test runs never change the official numbers | Automated, preventive | Every run | Unit tests; CI results | `cecl/environment.py`, dbt `assert_environment` | 0 | built |
| C14 | Blocked windows: no run starts Monday before 13:00 or 02:30–04:00 Chicago | Bank Health's run and the VM reboot are never disturbed | Automated, preventive | Every DAG change (CI) and every task start | CI `dag-import` result; task logs | `cecl/scheduling.py`, `ingestion/preflight.py` | 0 | built |
| C15 | Secrets and identifiers scan; no data files tracked | Nothing secret, identifying or licensed is published | Automated, preventive | Every commit and pull request | pre-commit and CI `secrets` results | gitleaks, `test_repo_hygiene.py`, CI `no-data` step | 0 | built |
| C16 | Aggregates only in published outputs | No loan-level rows leave the lakehouse (Freddie's terms) | Automated, preventive | Every site export | Export check result | `scripts/export_site_data.py` | 7 | planned |
| C17 | CI green before merge; branch protection on `main` | Every change is tested before it lands | Automated, preventive | Every pull request | GitHub checks history | `.github/workflows/ci.yml` | 0 | built (protection after publishing) |
| C18 | Freshness and run log: each run records rows per layer, duration, Databricks minutes, test results; stale data alerts | Runs happened, completely and on time | Automated, detective | Every run; weekly health check | Run log table; alert history | Phase 1 | 1 | planned |
| C19 | Disclosure tables tie to the rollforward and the book | Published tables agree with the numbers behind them | Automated, detective | Every quarter | Tie-out result | `cecl.disclosures` | 4 | planned |

## Quarterly sign-off

One block per quarter, filled in for the committee pack (`committee_packs/`).

| Quarter | Control | Result (pass / fail / exception) | Evidence (link) | Maker (date) | Checker (date) | Notes |
|---|---|---|---|---|---|---|
| *e.g. 2026 Q2* | C01 | | | | | |

An exception (a failed or skipped control) needs a note, a limitations-log entry if it reveals a
weakness, and a fix date.
