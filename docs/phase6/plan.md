# Phase 6 plan: validation

**Status (20 November 2026): decisions taken (end of this file); final test designs and pass/fail rules in `docs/phase6/test_designs.md`; waiting for Martins's written expectations (`docs/phase6/backtest_expectations.md`). Nothing run.**

Educational model on Freddie Mac conforming mortgages (sample book); not an audited reserve and not
accounting advice. Model v2 is `@challenger`. It failed its holdout (L-038), and the check that measured
it was biased (L-039). The reserve carries no L-038 overlay (Option B). Every Phase 6 output states all
three.

## What Phase 6 must show (roadmap v2, Section 7)

1. Crisis backtest (train before 2007), with an honest write-up.
2. Rolling backtest: the reserve held at each past quarter against the losses that followed.
3. Forbearance treatment test.
4. Peer comparison with real banks' reserve ratios, with the differences explained.
5. Model card, validation report and a validation pack for a human reviewer.
6. Model inventory entry completed, and the limitations log filled in (each issue with an owner and a
   fix date).

Also carried in: L-043 (rate shocks lower the reserve in both directions, Phase 5).

**Rule for every test in this phase:** the test design, what is compared and what we expect are written
and committed **before** the test runs, as for the holdout. These tests are **information for the
validator**: they do not change the booked reserve or the model status. Any fix becomes a v3 item.

## 1. Crisis backtest (train before 2007, test 2007-2010)

What it answers: if a bank had built this model at the end of 2006, how far off would its loss forecast
for the crisis have been?

Proposal:
- **PD and payoffs:** the v2 recipe (logistic transition models, same inputs and settings), refitted on
  months before January 2007 only (sample files).
- **LGD:** the Phase 3 two-step LGD recipe, refitted on defaults resolved before 2007. Few pre-2007
  defaults have losses, so this LGD will be thin; the write-up says so.
- **EAD:** the booked schedule and partial-prepayment rates (no crisis information in them).
- **Projection:** from the book alive at 31 December 2006, 48 months (January 2007 to December 2010), on
  **the economy as it actually happened**. This tests how the model responds to a real crisis, not
  whether anyone could have forecast it.
- **Compared:**
  - projected against actual defaults, by year, vintage and starting state;
  - projected expected loss against actual losses (Freddie's loss fields, resolved by June 2026);
  - the same for a simple benchmark: the long-run average default rate by vintage age.

What we already know: v2's walk-forward crisis exam (F1, trained to 2007) projected 0.38 of actual 2008-2010
defaults, or 0.50 counted correctly (L-035, L-039). Expect the backtest to under-project badly. The point
is to measure and explain the gap, not to pass.

## 2. Rolling backtest

What it answers: at each past quarter-end, was the reserve the model would have held enough for the
losses that followed?

Proposal:
- **Dates:** every year-end from 2012 to 2022 (11 dates). A quarterly grid would be about 44 dates and
  4 times the compute, for little extra information. Dates stop at 2022, so at least 3 years of
  following losses are observed by June 2026.
- **Models:** the booked ones (v2, Phase 3 LGD and EAD). They were fitted on data up to mid-2024, so these
  dates are **in sample**. The test therefore checks the engine's arithmetic and calibration over time;
  it is not an out-of-sample forecast test. Refitting the models for each date would be honest but
  costs 11 trainings; that is an option below.
- **Economy:** two versions.
  - (a) As it actually happened (the "perfect forecast" reserve).
  - (b) Flat at the reserve date's values, then reverted (a simple point-in-time forecast, because no
    historical Fed scenarios before 2025 are loaded).
- **Compared, for each date:**
  - the 36-month projected loss against the actual losses on that book over the next 36 months (like for
    like);
  - the lifetime reserve against the cumulative actual losses to June 2026, a partial lifetime;
  - a chart of the reserve ratio against the following loss rate over time.

## 3. Forbearance treatment test

What it answers: how much does ADR-002's choice to stop the delinquency clock in disaster or COVID
forbearance change defaults and the reserve? The forbearance flags exist only from 2014 (L-016).

Proposal:
- **Default counts 2020-2021**, under the booked definition (forbearance stops the clock) and with the
  flag ignored (forborne missed payments count as late, so 90+ days late becomes a default). Shown by
  month, by vintage, and how many of the extra "defaults" later cured.
- **The reserve at 30 June 2020** (book alive then, actual economy), both ways:
  - each loan's starting state taken with and without the flag;
  - the realised 2020-2023 losses compared with each.
- **Expected:** many forborne loans cured, so ignoring the flag overstates defaults and understates the
  loss per default (L-003).

## 4. Peer comparison (Q9)

What it answers: is our reserve ratio in a sensible range compared with real lenders? A sanity range,
not a target (L-001).

Proposal (the plan is in `docs/phase6/peer_comparison_plan.md`):
- **Banks:** the allowance on 1-4 family residential loans as a % of those loans, from FDIC call reports
  (Schedule RI-C, filed by banks with $1bn or more in assets), read-only from Bank Health's data.
  - Shown at 30 June 2026 and over time, as the median and middle 50% by bank-size group.
- **Freddie Mac's own ratio:** 0.19% at 30 June 2026; its quarterly releases, collected by hand.
- **Ours:**
  - the total allowance ratio (0.35%);
  - the baseline-only ratio (0.15%);
  - with the LGD conservatism removed (0.21%).
- **Explained:** book mix (jumbo and non-conforming loans in banks' books), mortgage insurance, LGD
  conservatism, scenario weights.

## 5. L-043: rate shocks

Break the Phase 5 rate-shock change down by loan group: loan-to-value band, vintage, and note rate
against the market rate. That shows whether v2's rate inputs move defaults towards loans that lose
little. Pricing of the existing Phase 5 runs only; no new projection.

## 6. Documents

- **Validation report:** a Phase 6 section with each test's design (committed first), its result and the
  explanation, plus an overall conclusion on fitness for use.
- **Model card:** update for v2, LGD, EAD and the engine: purpose, data, limits, results.
- **Validation pack:** the Bank Health format, a single document a human reviewer can work through.
  - Contents: scope, tests, evidence links, open findings, a reviewer sign-off table.
- **Model inventory:** entries M-01 to M-07 completed, with risk tier, owner, status, validation date and
  next review.
- **Limitations log:**
  - every open item reviewed, with an owner and a fix date;
  - items closed with evidence where possible, for example L-042 once quarter-end snapshots start.

## Compute and timing

| Work | Databricks | PC |
|---|---|---|
| Crisis backtest: refit (3 transition models, LGD) and one 48-month projection | about 15-20 minutes | pricing about 15 minutes |
| Rolling backtest: 11 dates x 2 economies = 22 projections | about 25 minutes | pricing 22 runs at 4-12 minutes each, about 2-4 hours |
| Forbearance test: counts and 2 projections at 30 June 2020 | about 5 minutes | pricing about 20 minutes |
| L-043 breakdown | none | about 15 minutes (re-price 3 runs with group sums) |

About 50 Databricks minutes in total, spread over two days, because Free Edition's quota is shared with
Bank Health. Never on a Monday before 13:00 or between 02:30 and 04:00.

## Decisions needed from Martins before building

1. **Crisis backtest design:** PD (v2 recipe) and LGD refitted on data before 2007, EAD as booked,
   projected from 31 December 2006 for 48 months on the actual economy, against a simple benchmark. It is
   information only, with expectations written before it runs.
2. **Rolling backtest:**
   - dates: every year-end 2012-2022 (11), or quarterly;
   - models: the booked models (in sample, disclosed), or a refit for each date (honest out of sample,
     about 11 trainings);
   - economy: both actual and flat-then-reverted (proposal).
3. **Forbearance test:** default counts in 2020-2021 both ways, and the 30 June 2020 reserve both ways
   (proposal).
4. **Peer comparison data:** where Bank Health's call-report data is (Databricks catalog and schema, or
   files on the VM), and whether it includes Schedule RI-C (the allowance by loan type). If it doesn't,
   options are: Martins adds RI-C to Bank Health's collection, the FDIC's public data is used directly, or
   the comparison uses total loan allowance ratios only. That last option is a weaker comparison.
5. **Expectations:** Martins writes his expectations for the crisis and rolling backtests (as for the
   holdout) before they run, or delegates the wording.

## Decisions (Martins, 20 November 2026)

1. **Crisis backtest:** approved as proposed.
2. **Rolling backtest:** every year-end 2012-2022 (11 dates); both economies (as it happened, and flat
   then reverted). Use refits, not the booked models: refit the logistic PD models and the LGD model for
   each date using only data available at that date (logistic only, no LightGBM). Report the
   Databricks minutes first; if 11 refits exceed about 120 minutes, propose every other year instead.
   (Estimate: about 75 minutes for the rolling backtest, about 90 for Phase 6, so all 11 dates.)
3. **Forbearance test:** approved as proposed.
4. **Peer data:** check (read-only) whether Bank Health's call-report data includes Schedule RI-C and
   where it lives; do not modify Bank Health. If RI-C is missing, CECL downloads the public FFIEC / FDIC
   call-report data itself, in its own pipeline (aggregates only, read-only use). No fallback to
   total-loan ratios. (Result: RI-C is missing from Bank Health, and the FDIC API lacks it; see
   `docs/phase6/test_designs.md`.)
5. **Expectations:** Martins writes his expectations for the crisis and rolling backtests before they
   run. The final test designs and pass/fail rules are committed first, then the work stops.
