# ADR-012: Quarterly close automation and model monitoring (Phase 8)

- **Status:** proposed, 22 November 2026 (Martins's choices recorded below; the Decision section is his to write)
- **Related:** ADR-001 (tools and data sizing: Freddie files by hand), ADR-004 (environments and the shared
  VM), ADR-005 (weights), ADR-006 (overlays), ADR-009 (calibration bars), the controls matrix, the roadmap v2
  changes ("Phase 8: drift and performance monitoring", approved 18 November 2026)

## Context

Phase 4 built the reserve on Martins's PC: the Databricks job `reserve_projection` produced each loan's
default and payoff chances, and `scripts/build_reserve.py` priced them with LGD and EAD from local files and
built the rollforward, bridge and disclosures. That cannot run unattended: the VM can't reach the PC, and the
PC is not part of prod. Phase 8 needs a close that runs in prod after each Freddie release, stops on any
control failure, produces the committee pack, keeps a quarter-end snapshot, and is monitored between closes.

## Choices made by Martins (22 November 2026)

| Question | Choice |
|---|---|
| Where the pricing runs in prod | **A Databricks job** (`close_pricing`, notebook 68), the same code as the PC (`cecl/close.py`); the PC's `build_reserve.py` stays the check |
| How results reach the public repo | **Draft, then PR**: the close writes aggregates and a draft committee pack; a reviewed PR with the sign-off page commits them. No GitHub credentials on the VM |
| How a close starts | **Martins triggers it** (`cecl_quarterly_close`, with the quarter-end) after downloading and uploading Freddie's files |
| Scope of Phase 8 now | **The close and the monitoring** (weekly score stability and quarterly calibration) |

## Design

**One code path.** The calculations moved from `scripts/build_reserve.py` into `cecl/close.py`, general for
any quarter-end. Functions that read loan data take a DuckDB connection and relation names, so the PC
(Parquet files) and Databricks (small frames pre-filtered by Spark from the prod tables) run the same SQL.
Checked on 22 November 2026: the refactored PC script reproduces every committed Phase 4 figure to the cent,
and a fresh re-pricing through `close.price` is identical loan by loan.

**The close rolls forward from what was booked.** Each close saves its booked frame (each loan's booked
reserve and group) and its priced files in the private volume (`results/close/<R>/`). The next close starts
its rollforward from that frame, so the start always equals the previous booked end; the job checks it to the
cent and stops otherwise. The Phase 4 closes (31 December 2025, 31 March and 30 June 2026) are seeded once from
the PC (`scripts/seed_close.py`), each checked against the committed totals before upload.

**Controls before anything is booked** (any failure stops the close): the quarter is the next one after the
last close; this quarter's overlay review is in the committed overlay log (ADR-006: overlays expire every
quarter); every prod job was deployed from the commit the VM runs (job tag `git_commit`); every year's Freddie
file is in the volume and newer than the last close; no blocked window begins within the close's 4 hours;
then, in the pricing job, every tie-out, the rollforward to the cent, its start against the previous booked
end, and the bridge reconciling and tying to both totals.

**Quarter-end snapshot.** Each close record holds the Delta versions of the tables it read, the registry
versions and training runs of the models, the hashes of the LGD model and the overlay log, and the code
commit; the PR that commits the record is tagged `cecl-<YYYYQn>-close`. Together they rebuild the quarter.

**Monitoring.** Weekly (Wednesdays, after the economic data): freshness from what the VM already holds; when a
new economic month has arrived, the live book is scored with the latest economy and each transition's score
distribution is compared by PSI with a stored baseline profile (deciles and shares only). Quarterly (part of the
close): predicted / actual per transition by year, origination year and starting state with the ADR-009 bar,
AUC, a trend point and the written retrain rule (`cecl.monitoring.retrain_due`).

## Points for Martins to confirm in the Decision

1. **The weekly PSI baseline** is the book at 30 June 2026 scored one month ahead (the reserve the v2 models
   first served), not the training sample: the training rows are sampled and weighted months from 1999-2016,
   a different population from a live book. Alternative: profile the training rows instead.
2. **Quarterly calibration groups** are year, origination year and starting state (current / 30 / 60). The
   ADR-009 late-history bands are not rebuilt for the live quarter in this version.
3. **The site follows a close** only after the PR is merged (the site pages and schemas still name the 2026
   dates; making them date-generic is the next Phase 8 step). Until then the close does not publish.
4. **The VM needs `DATABRICKS_HOST` and `DATABRICKS_TOKEN`** in its .env (a token that can run the CECL jobs
   and read and write the CECL volume, nothing else).

## Decision

*(Martins writes this section in his own words.)*

## Consequences

- The prod reserve no longer depends on the PC; the PC remains the independent check of the pricing.
- A failed control stops the close before anything is booked or published, and alerts.
- Every quarter's close record, committee pack and snapshot are committed through a reviewed PR.
- Databricks minutes per close: about 140-160 (Freddie reload about 55, panel about 40, projection about 20,
  pricing about 15, monitoring about 15); weekly scoring about 3-5 when a new economic month arrives.
