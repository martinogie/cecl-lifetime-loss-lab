# Architecture Decision Records

One short note per major decision: the context, the options, the decision and its consequences.
Martins writes each Decision section in his own words.

| ADR | Topic | Status |
|---|---|---|
| [001](001-tools-and-data-sizing.md) | Tools and data sizing: sample files for development; full data only for 2005–2008 and 2019 (COVID; optionally 2017); read from the zips, never unzip; manual Freddie downloads; shared VM capacity check | accepted, 14 November 2026 |
| [002](002-definition-of-default.md) | Definition of default: 90+ days, `RA` or loss codes; codes 15/16/96; first default only; modifications at 60+ days; stop-the-clock forbearance; `XX` carried forward up to 3 months | accepted, 14 November 2026 |
| [003](003-forecast-period-and-reversion.md) | Forecast period and reversion: 2 years, then 1-year straight-line reversion of the economic inputs to long-run averages (1990 onward); Fed paths shifted to start in July 2026 (amended 15 November 2026) | accepted, 14 November 2026; amended 15 November 2026 |
| [004](004-environments-and-shared-vm.md) | Environments and shared VM: own Airflow stack beside Bank Health, rootless Docker, caps, blocked windows, two catalogs (fallback if needed), manual deploys; credentials pending | accepted, part D pending (service principals) |
| [005](005-scenario-weighting.md) | Scenario weighting: halfway adverse path; weight the losses; fixed 60/30/10 reviewed yearly; severe weight floor 5%, cap 25% | accepted, 14 November 2026 |
| [006](006-overlay-rules.md) | Overlay rules: a named 2020-statement factor; sized by re-running the model; caps 5% each and 10% total; decreases need extra sign-off; quarterly expiry; maker/checker | accepted, 14 November 2026 (factor wording to confirm) |
| [007](007-sample-weighting.md) | Sample weighting: the sample is drawn equally by quarter; no approximate weights; models and the reserve use the sample as it is; weighted comparison later for the full-data vintages; effect on model training | accepted, 15 November 2026 |
| [008](008-state-scenario-mapping.md) | State scenario mapping: turning the Fed's national unemployment and house-price paths into 51 state paths: each state's historical sensitivity (1991 onward), with limits on extreme values | accepted, 15 November 2026 |
