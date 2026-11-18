# Validation report: CECL Lifetime Loss Lab

Draft, built up phase by phase. Educational model on Freddie Mac conforming mortgages; not an
audited bank reserve and not accounting advice. Every figure below is on the **validation years
2017-2019**; the out-of-time test months (July 2022 - June 2026) have not been scored.

## Phase 2: multi-state transition models (ADR-011, ADR-009 Amendment 1)

**Status:** draft, 18 November 2026, for Martins's review **before** the out-of-time test.

### How validation informed the design (stated up front)

Validation was not a single look at the end. It changed the design twice, and both changes are
recorded:

1. The first single-step default model calibrated badly on 2017-2019 (1.4-1.5x too many defaults);
   the diagnostics (ADR-010) pointed to higher cure rates among already-delinquent loans, which led
   to the multi-state design (ADR-011): one model per starting state (current, 30, 60 days late).
2. The first 2017-2019 projection check of that design projected 6.2x too many defaults. The cause
   was a flaw in the projection code, not in the models: the input "months late in the past 12" was
   fed as an expected value (a fraction such as 0.02 for a loan never late in the projection), and
   the current-loan model reads that input in steps (split at 0; roll rate 0.35% vs 9.4% a month),
   so every clean loan was scored as recently late from month 2. The projection now carries the
   input as a whole number (limitations log L-026; commit 100e781). The models were not retrained
   and the selection did not change.

Because the validation years were used to find and fix that flaw, the 2017-2019 projection result
is no longer a fully independent check of the projection. The out-of-time test is the independent one.

### Data and tie-outs

- Transition table: every live loan-month 1999-2019 with its starting state and next state;
  current -> current months sampled 1 in 25 and weighted, every other move kept whole.
- The full build passed all 609 tie-outs (every move vs the panel, every live loan-month in exactly
  one starting state, moves into default and prepaid vs the panel) and the data contract.
- The PC slice matched Databricks exactly (615,532 transition rows; 15,560 projection rows).
- DTI is blank in 9.6% of rows (limit 10%; L-024). No 60-days-late -> prepaid moves exist before
  2019 (L-027).

### The nine models (one-step results on 2017-2019)

Log loss is the step's own log loss across all destinations (lower is better). The calibration bar
is 0.80-1.25 (predicted / actual) on every cell with at least 200 actual moves, by year, by vintage
and over the whole period.

| Starting state | Family | Log loss | vs logistic | AUC of the adverse move (Gini) | Cells failing |
|---|---|---|---|---|---|
| 60 days late | **Logistic** (C = 1.0) | 1.3225 | | 60 -> default 0.670 (0.34) | 0 |
| | LightGBM (15 leaves, min weight 50) | 1.3754 | 4.0% worse | 0.663 (0.33) | 4 |
| | EBM (defaults) | 1.3229 | 0.03% worse | 0.675 (0.35) | 0 |
| 30 days late | **Logistic** (C = 0.1) | 0.9237 | | 30 -> 60 0.550 (0.10) | 1 (prepaid 2017, 1.33) |
| | LightGBM (31 leaves, min weight 200) | 0.9198 | 0.4% better | 0.576 (0.15) | 4 |
| | EBM (defaults) | 0.9171 | 0.7% better | 0.578 (0.16) | 4 |
| Current | **Logistic** (C = 1.0) | 0.07588 | | current -> 30 0.811 (0.62) | 12 |
| | LightGBM (15 leaves, min weight 200) | 0.07262 | 4.3% better | 0.818 (0.64) | 6 |
| | EBM (defaults) | 0.07291 | 3.9% better | 0.819 (0.64) | 7 |

Settings were chosen by walk-forward tuning on the training years only (three folds: test 2008-2010,
2011-2013, 2014-2016); every fold's score is in MLflow.

**Fit:** every model's training-vs-validation verdict is "mild gap". Learning curves are flat from
10% to 100% of the training rows, so more data would not help. For current and 30, validation log
loss is lower than training because 2017-2019 was a calm period; the gap is a shift between periods,
not overfitting. Score stability (PSI) for the 30-day step is 0.7-1.1, well above the usual 0.25
alarm: the 2017-2019 30-day-late loans look different from earlier ones (forbearance and loss
mitigation are the likely reasons).

### Selection (ADR-009 Amendment 1)

Logistic is kept at every step:

- 60 days late: neither challenger beats logistic.
- 30 days late: the challengers improve log loss by under 1%, short of the 2% required.
- Current: both challengers are about 4% better but fail calibration cells, so neither qualifies.
  Logistic also fails cells; under the rule it stays, and its failures are recorded here.

With logistic chosen at every step the combination check has nothing to swap. Almost every failing
cell is **prepayment from current, by vintage** (over-predicted 1.4-1.5x for the 2017-2019 vintages,
under-predicted 0.6-0.7 for 2003 and 2012).

### Projection check, 2017-2019 (all-logistic combination)

Every loan alive in January 2017 (278,373 loans) is played forward 36 months with the actual
economy; censored loans count only their observed months. Databricks run at commit 100e781
(MLflow run `829e0d811157419f934d4aef59fe4196`, 5.3 minutes).

| | Projected | Actual | Ratio | Before the fix |
|---|---|---|---|---|
| Defaults | 2,912 | 3,162 | **0.92** | 19,719 (6.2x) |
| Payoffs | 67,343 | 83,789 | **0.80** | 56,907 (0.68x) |

| Year | Defaults projected / actual (ratio) | Payoffs projected / actual (ratio) |
|---|---|---|
| 2017 | 1,330 / 1,249 (1.06) | 31,729 / 31,862 (1.00) |
| 2018 | 869 / 1,050 (0.83) | 16,648 / 25,595 (**0.65, fails**) |
| 2019 | 714 / 863 (0.83) | 18,966 / 26,332 (**0.72, fails**) |

- **Defaults pass every cell** (by year and by vintage; the five vintages with 200+ defaults,
  2012-2016, are at 0.91-1.09).
- **Payoffs fail 11 cells:** 2018 and 2019 by year, and nine vintages: 1999 (1.35), 2001 (1.31),
  2002 (0.54), 2003 (0.49), 2004 (0.66), 2010 (0.70), 2011 (0.67), 2012 (0.55), 2013 (0.70).
  Vintages 2005-2009 and 2014-2016 pass.

**Month by month (defaults).** The same projection rerun on the PC at commit 100e781 on the full
start and actuals tables reproduces the Databricks totals to five decimal places, and gives monthly figures:

| Period | Projected defaults a month | Actual defaults a month |
|---|---|---|
| 2017 (Jan-Dec) | 95-126, average 111 | 81-140, average 104 |
| 2018 Jan-Feb | 103, 93 | 89, 104 |
| 2018 Mar-Dec | 64-74, average 67 | 73-96, average 86 |
| 2019 | 48-71, average 59 | 59-91, average 72 |

The projection tracks 2017 month by month, with no jump. From March 2018 it runs about 20-25%
below actual. (These monthly figures are from the faded-history run, 100e781. With the exact history,
9548682, they are almost the same: 2017 average 108 a month, March-December 2018 average 66, 2019
average 59. See below.)

**By starting state (defaults over 2017-2019):**

| Starting state in January 2017 | Loans | Projected | Actual | Ratio |
|---|---|---|---|---|
| Current | 275,952 | 2,388 | 2,377 | 1.00 |
| 30 days late | 2,040 | 355 | 529 | 0.67 |
| 60 days late | 381 | 169 | 256 | 0.66 |

The whole default shortfall is in loans that were already late at the start. Loans that start
current are projected almost exactly.

**Tested and ruled out: the faded start history.** The projection then started from each loan's
actual late-payment history (its 12 panel rows before January 2017, a start month leaving the window
exactly when it would in the data; commit 9548682; tie-out: all 278,373 loans' flags add up to their
late_months_12). Databricks MLflow run `8f678210bfb848e4a08f61f09cb36dcd`:

| Starting state | Actual | Faded history (100e781) | Exact history (9548682) |
|---|---|---|---|
| Current | 2,377 | 2,388 (1.00) | 2,335 (0.98) |
| 30 days late | 529 | 355 (0.67) | 358 (0.68) |
| 60 days late | 256 | 169 (0.66) | 169 (0.66) |
| All | 3,162 | 2,912 (0.92) | 2,863 (0.91) |

The shortfall for loans late at the start, and the March 2018 step down (projected 62-73 a month
for March-December 2018 against 73-96 actual), are essentially unchanged. So the cause lies elsewhere:
most likely the 30- and 60-day models themselves, for loans already behind in January 2017. Those
loans differ from earlier late loans (score stability for the 30-day step is 0.7-1.1). Not yet
diagnosed. Every default cell still passes (2018 by year: 0.81; 2019: 0.82). The exact history is
kept, because it matches the data's definition.

### Payoffs are under-projected after the first year (limitations log L-028)

The projection matches 2017 payoffs exactly but falls to 0.65-0.72 of actual in 2018-2019, mostly in
the older refinance-era vintages (2002-2004, 2010-2013). The one-step model shows the same pattern
by vintage (seasoned loans under-predicted, new 2017-2019 loans over-predicted); in the one-step
results the two partly cancel in the by-year cells, but the projection holds only loans already open
in January 2017, so the under-prediction shows. Likely reasons: the payoff model has no input for
home-sale turnover or burnout, so loans with little refinance incentive (as in 2018, when mortgage
rates peaked) are given too low a payoff rate.

**Effect on the reserve:** under-projected payoffs keep loans on the book longer, so lifetime
expected loss is somewhat **overstated** (conservative). The projected defaults already include this
effect and still come out at 0.92 of actual. Recorded as L-028; candidate fixes (a turnover or
seasoning input, or a payoff level adjustment) are for a later model change, not for this test.

### COVID sensitivity

The three logistic models refitted with the 2020-2021 months included, at the same settings:

| Starting state | Log loss without -> with COVID | Cells failing without -> with |
|---|---|---|
| Current | 0.07588 -> 0.07560 | 12 -> 13 (all payoffs by vintage or year) |
| 30 days late | 0.92369 -> 0.92359 | 1 -> 3 (payoffs) |
| 60 days late | 1.3225 -> 1.2798 | 0 -> 1 (cures, vintage 2017, 1.29) |

Default calibration is unaffected at every step. The planned training years (without the COVID
months) are kept; this is recorded as a sensitivity result.

### Still to come

- The out-of-time test (July 2022 - June 2026), scored once, only after Martins approves it.
- Registration of the @challenger, model card and model inventory entries (step M9).
