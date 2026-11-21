# Phase 6 test designs and pass/fail rules (committed before any test runs)

- **Status:** final designs, 20 November 2026, from Martins's decisions of 20 November 2026
  (`docs/phase6/plan.md`). Committed **before** any Phase 6 test runs and before Martins's written
  expectations (`docs/phase6/backtest_expectations.md`). Once the expectations are committed, each test
  runs **once** under these rules. A re-run needs Martins's written approval.
- **What the results do:** they are evidence for the validator. They do not change the booked reserve,
  the model status (v2 stays `@challenger`) or Option B. Any fix becomes a model v3 item.

Educational model on Freddie Mac conforming mortgages (sample files); not an audited reserve and not
accounting advice.

## Rules shared by every test

- **Counting:** projected chances are counted up to censoring only (the corrected count, L-039,
  `cut_at_exit=False`). Defaults follow ADR-002, as everywhere.
- **Actual loss of a default:** Freddie's reported net loss (zero for a gain). A default with no
  reported outcome by June 2026 (unresolved) is valued at the booked Phase 3 LGD. The share of loss
  valued that way is shown next to every result.
- **The bar:** "within the bar" means a projected / actual ratio between **0.80 and 1.25**, the bar used
  for the holdout. For single years or single dates, the wider "reasonable" band is **0.67 to 1.50**.
- **Path simulation:** 8 paths per loan, seed 7, starting from each loan's actual state at the start date
  (`first_state`), as in the reserve engine.
- **Run log:** each run records its code commit, data snapshot, Databricks minutes and MLflow run.

## CB. Crisis backtest (train before 2007, test 2007-2010)

| Item | Rule |
|---|---|
| PD and payoffs | The v2 recipe: logistic transition models at the current, 30-day and 60-day steps, the 28 v2 inputs, C = 1.0 (v2's chosen setting; not re-tuned). Fitted on months up to December 2006 only |
| LGD | The Phase 3 two-step recipe (chance of any loss, then severity), refitted on defaults whose loss Freddie had reported by December 2006 |
| EAD | Booked (payment schedule and partial-prepayment rates) |
| Book | Sample loans alive at 31 December 2006, starting from their actual state |
| Projection | 48 months, January 2007 to December 2010, on the economy as it actually happened |
| Benchmark | Monthly default rate by loan age, averaged over 1999-2006, applied to the same book with its survival. Benchmark loss = benchmark defaults x balance at default x the average loss per $ defaulted on pre-2007 resolved defaults |
| Compared | Projected / actual defaults and projected expected loss / actual loss: in total, by year (2007-2010), by vintage and by starting state, for the model and for the benchmark |

**Pass rule:**
- (1) The total defaults ratio is within the bar.
- (2) The total loss ratio is within the bar.
- (3) The model is closer to actual than the benchmark, on both defaults and losses (smaller distance of
  the ratio from 1, measured in log terms).

The test passes only if all three hold. Each year's ratio is reported against the 0.67-1.50 band for
information.

## RB. Rolling backtest (year-ends 2012-2022, refitted at each date)

| Item | Rule |
|---|---|
| Dates | 31 December of each year 2012-2022 (11 dates) |
| PD and payoffs | At each date: the v2 recipe (logistic only, C = 1.0, the 28 inputs), refitted on months up to that date only. The COVID months (March 2020 - June 2022) are left out of fitting, as for v2 |
| LGD | At each date: the Phase 3 two-step recipe, refitted on defaults whose loss Freddie had reported by that date |
| EAD | Booked |
| Book | Sample loans alive at the date, starting from their actual state |
| Economy (a), "as it happened" | The real economy after the date |
| Economy (b), "flat then reverted" | Unemployment and the mortgage rate held at the date's values, and each state's house prices growing at their trailing 12-month rate, for 24 months. Then 12 months of straight-line reversion to long-run averages computed with data up to that date only (point in time) |
| Measures per date | (i) Projected expected loss over months 1-36 against the actual losses on that book from defaults in months 1-36 (like for like). (ii) Projected / actual defaults over months 1-36. (iii) The lifetime reserve against cumulative actual losses up to June 2026 (a partial lifetime; information only) |

**Pass rule, judged on economy (a):**
- (1) Summed over the 11 dates, projected / actual 36-month loss is within the bar.
- (2) At least 8 of the 11 dates are within the 0.67-1.50 band.

Economy (b) is reported the same way for information. It measures forecast skill with a naive forecast,
not the model. The 2022 date's 36 months end in December 2025, so more of its losses are unresolved; its
unresolved share is shown.

## FT. Forbearance treatment test

| Item | Rule |
|---|---|
| Counts | Monthly ADR-002 default counts, January 2020 - December 2021: (A) the booked definition, where forbearance stops the delinquency clock; (B) the flag ignored, so forborne missed payments count as late and 90+ days late is a default. By month and vintage. Of the defaults counted only under (B): the share current again within 12 months, and the share that later had a loss |
| Reserve | The book alive at 30 June 2020, projected 36 months on the economy as it happened, with the booked v2 models (v2 did not fit the COVID months; disclosed) and the booked LGD and EAD. Starting states taken under (A) and under (B). Each 36-month projected loss is compared with the actual losses on the same book from defaults in July 2020 - June 2023, counted under the same definition |

**Pass rule:** the booked definition (A) is supported if both hold:
- (1) Its 36-month projected / actual loss ratio is closer to 1 than (B)'s.
- (2) At least half of the defaults counted only under (B) were current again within 12 months.

If either fails, ADR-002's forbearance rule is flagged for review in v3.

## L-043. Rate-shock breakdown (no new projection)

The Phase 5 baseline and the ±2-point rate-shock runs are re-priced with loss sums by group:
- loan-to-value band;
- vintage;
- note rate against the market rate at June 2026.

It shows where the change in defaults and losses comes from. Information only: L-043 is closed if the
breakdown explains the direction, and otherwise stays open for v3.

## Peer comparison (Q9): data

Read-only check of Bank Health (20 November 2026):
- `bank_health.silver.fdic_financials` and `bank_health.bronze.fdic_financials` hold no allowance fields,
  so they don't have Schedule RI-C. Bank Health is not modified.
- The FDIC's public API (`api.fdic.gov/banks/financials`, no key) has the total allowance and residential
  loans, but not the allowance on 1-4 family loans. Its real-estate allowance field (`LNRESRE`) is empty
  for 2026.

So CECL loads the FFIEC call-report data (Schedule RI-C, the allowance by loan category, filed by
institutions with $1bn or more in assets) in its own pipeline, with a data contract. Only aggregates are
kept: the median and middle 50% by bank-size group.

Built as the official FFIEC Public Web Service pipeline (`ingestion/ffiec.py`; Martins, 20 November 2026). The measure is RI-C item 1.c, residential real estate loans as each bank segments its allowance: 1-4 family first and junior liens and home-equity lines, with multifamily at some banks. It is the closest public measure to a 1-4 family mortgage reserve ratio, and the memo says so.

Comparison rule (no pass/fail; a sanity range): our ratios are shown against the bank distribution and
Freddie's 0.19%. The memo says "inside", "below" or "above" the middle 50%, with the reasons.

## Compute estimate (Databricks)

From the measured v2 runs: one logistic training with tuning took 17.8 (current), 4.5 (30-day) and 1.8
(60-day) minutes, about 10 fits each, so one fit at all three steps costs about 2-3 minutes. A projection
of one book costs about 1 minute, plus about 2 minutes to build the book.

| Test | Estimate |
|---|---|
| Rolling backtest: 11 dates x (refit 3 steps about 3 + book about 2 + 2 projections about 2) | **about 75 minutes** (range 60-95) |
| Crisis backtest: refit, book, 48-month projection | about 8 minutes |
| Forbearance test: counts, 2 projections | about 6 minutes |
| **Total** | **about 90 minutes**, under the 120-minute limit, so all 11 dates |

Run in batches across two days, each started outside the blocked windows (not Monday before 13:00, not
02:30-04:00) and never alongside Bank Health's Monday run. LGD refits and pricing run on the PC (about
3-5 hours in total).
