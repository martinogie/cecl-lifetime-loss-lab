# Changes to roadmap v2 (decided after it was written)

The roadmap PDF is private (prepared for Martins, kept on his PC, never committed). Decisions that
change what it describes are listed here, each with its ADR.

| Date | Roadmap v2 says | Changed to | Where |
|---|---|---|---|
| 15 Nov 2026 | COVID full-data vintage 2020 or 2021 | 2019 (optionally 2017) | ADR-001 change note |
| 15 Nov 2026 | Fed paths from July 2026 as changes from the Fed's 2026 Q2 value | Fed path shifted to the reserve date (its first quarter becomes 2026 Q3) | ADR-003 amendment |
| 16 Nov 2026 | Phase 2 training on the PC | Features and training on Databricks, the PC as the reference | ADR-001 change note; Phase 2 plan |
| 17 Nov 2026 | Section 5.1: a competing-risks hazard model (default and prepayment each month) | **Extended to a monthly multi-state (delinquency-transition) model:** current, 30, 60, 90+ default (absorbing), prepaid, with cures; logistic, LightGBM and EBM fitted for every starting state (9 models), the ADR-009 rule choosing at each step | ADR-011; ADR-009 Amendment 1 (proposed); docs/phase2/plan_multistate.md |
