# Architecture Decision Records

One short note per major decision: the context, the options, the decision and its consequences.
Martins writes each Decision section in his own words.

| ADR | Topic | Status |
|---|---|---|
| [001](001-tools-and-data-sizing.md) | Tools and data sizing: the shared VM's size and free capacity, Databricks Free Edition limits, sample files vs chosen full vintages | draft: VM check and decision pending |
| [002](002-definition-of-default.md) | Definition of default: 90+ days delinquent or a loss event; zero balance codes; one default per loan or re-defaults; modifications; disaster and COVID forbearance | draft: decision pending |
| [003](003-forecast-period-and-reversion.md) | Forecast period and reversion: forecast length; immediate or straight-line reversion; revert the economic inputs or the loss rates; long-run window | draft: decision pending |
| [004](004-environments-and-shared-vm.md) | Environments and shared VM: own Airflow stack beside Bank Health, rootless Docker, caps, blocked windows, catalogs vs fallback, credentials, manual deploys | draft: decision pending |
| [005](005-scenario-weighting.md) | Scenario weighting: building the adverse path; weighting losses vs inputs; how weights are set; the weight-sensitivity table | draft: decision pending |
| [006](006-overlay-rules.md) | Overlay rules: each overlay tied to a qualitative factor from the 2020 interagency allowance policy statement, quantified, capped, logged, approved, expiring | draft: decision pending |
