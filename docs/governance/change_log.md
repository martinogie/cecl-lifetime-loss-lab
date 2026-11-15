# Change log

Every change to a model, an assumption (forecast length, reversion, scenario weights, default
definition, long-run averages) or an overlay, with its date, reason and approval (control C09).
Code changes are also in git; this log records the *decision* behind each change to the reserve.

| Date | Area (model / assumption / overlay) | What changed | Why | Expected effect on the reserve | Link (PR, ADR, MLflow version) | Maker | Checker / approval (date) |
|---|---|---|---|---|---|---|---|
| 2026-11-14 | Assumption: default definition (ADR-002 E) | `XX` at loan age 0 (the month before the first payment is due) is read as "not yet due" and treated as current, instead of a missing status. `XX` at later ages still follows E | From October 2025 Freddie reports that month as `XX` (previously `00`). Read as missing, it censored 34,989 new loans (almost all of the 2025–2026 vintages) in their first month | All 28 vintages rebuilt: 1999–2023 identical; 2024 default rate (1.17%) and final-status counts identical, with 10 loans (first payment due Nov 2025–Jun 2026) kept in the panel instead of censored at once; 2025 and 2026 loans no longer censored in their first month (34,989 loans use the rule: 10 in 2024, 13,520 in 2025, 21,459 in 2026) | ADR-002 E clarification; `cecl/panel.py`; Phase 1 steps 3–5 pull request | Martins | Martins, approved 14 Nov 2026 |
