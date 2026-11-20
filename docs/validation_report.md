# Validation report: CECL Lifetime Loss Lab

Draft, built up phase by phase. Educational model on Freddie Mac conforming mortgages; not an
audited bank reserve and not accounting advice. Figures are on the **validation years 2017-2019**
unless marked as the out-of-time test (July 2022 - June 2026, scored once on 18-19 November 2026).

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

### The current-loan logistic refit and the late-history band bar (ADR-009 Amendment 2)

Scoring 2017-2019 by "months late in the past 12" showed a build mistake in the current-loan
logistic scorecard: its bins for that input collapsed to one cut (0 vs 1 or more), because about 98%
of current loan-months have the value 0 and the bins were cut at weighted quantiles. A loan late once
got 1.56x too many predicted rolls to 30, a loan late 3 or more times 0.37-0.58x. The errors cancel
by year and by vintage, so the existing bars could not catch it; in the projection they
under-projected defaults for loans late at the start. Amendment 2 (accepted 18 November 2026):

- **The refit:** whole-month cuts (0, 1, 2, 3, 5, 8) for that input in the current-loan logistic
  model (commit 963d5a9; MLflow run `bc66cf67e80c4722ba8461791045c90b`; tuning chose C = 0.1; log loss
  0.07588 -> 0.07567). Current -> 30 by band, before -> after: 0: 1.18 -> 1.21; 1: 1.56 -> 1.03;
  2-3: 0.70 -> 0.86; 4-5: 0.42 -> 0.73 (still fails); 6+: 0.37 -> 0.86.
- **The new bar:** every move at every step must also be within 0.80-1.25 by late-history band (0, 1,
  2-3, 4-5, 6+), where a band has 200+ moves.
- **Selection re-run (2017-2019 only):** logistic at every step. 60 days late: neither challenger is
  better. 30 days late: neither is 2% better, and both fail bars. Current: LightGBM (4.0% better) and
  the EBM (3.6%) pass the band bar but fail payoff calibration by vintage. Logistic's own failures are
  recorded (L-029) and tracked in monitoring.
- **Projection with the winners** (Databricks MLflow run `bd92a516887941f58dd574f5e2fb191f`):
  defaults 2,962 vs 3,162 (0.94), every default cell passes (by year 1.04 / 0.83 / 0.92; vintages
  2012-2016 0.90-1.22); payoffs 0.80, the same 11 failing cells (L-028).

| Starting state (Jan 2017) | Actual defaults | Faded history (100e781) | Exact history (9548682) | Refit (963d5a9) |
|---|---|---|---|---|
| Current | 2,377 | 2,388 (1.00) | 2,335 (0.98) | 2,365 (0.99) |
| 30 days late | 529 | 355 (0.67) | 358 (0.68) | 424 (0.80) |
| 60 days late | 256 | 169 (0.66) | 169 (0.66) | 173 (0.67) |
| All | 3,162 | 2,912 (0.92) | 2,863 (0.91) | 2,962 (0.94) |

Loans 60 days late at the start remained at 0.67 (L-030); diagnosed and retrained below.

### Retrain with three new inputs, and the last round of fixes (18 November 2026)

**What changed** (approved by Martins, 18 Nov 2026; change log): "state last month", home-price gain
since origination and SATO added to all three steps; the band bar extended to state last month; the
projection tracks the new inputs; the December 2016 double count fixed (commit faa0e3c). Full input
build: 609 tie-outs agree (14.2 Databricks minutes). All 9 models retrained back to back (about 216
Databricks minutes; the current-loan EBM alone 80.7). Test months untouched; no out-of-time test.

**Overlap check before retraining.** Home-price gain vs current LTV: correlation -0.45 (1999-2016) and
-0.47 (2017-2019), R-squared about 0.2. Moderate, so both are kept in every family; within an LTV band
payoffs still rise with home-price gain (LTV 60-80: 1.31% to 2.46% a month). LightGBM directions:
state last month raises defaults and lowers payoffs at the current step (free at 30 and 60, where the
pattern is not one-directional); home-price gain lowers defaults (payoffs free); SATO raises defaults
(payoffs free). Reasons in `cecl/features.py`.

**Selection (ADR-009 as amended, unchanged): logistic stays at every step.**

| Step | Logistic log loss, refit -> retrain | Logistic failing cells | Best challenger | Why it does not qualify |
|---|---|---|---|---|
| Current | 0.07567 -> 0.07545 | 15 -> 17 (3 roll-to-30, 14 payoff) | LightGBM 0.07241 (4.0% better) | 6 cells fail: 5 payoff vintages and roll-to-30 for loans 60 days late last month (1.28) |
| 30 days late | 0.92369 -> 0.90898 | 2 -> 2 | LightGBM 0.90188 (0.8% better) | under the 2% margin; 5 cells fail |
| 60 days late | 1.32252 -> 1.30648 | 0 -> 0 | EBM 1.30782 (not better) | not better; 1 cell fails (default vintage 2016, 1.25) |

MLflow runs (commit faa0e3c): current logistic `9f5ec09b9e624e1990c98c2af943a80a`, LightGBM
`79d4e92046504dbab6e09e82e2c3f378`, EBM `a4d81280b5384719894b375108d89266`; 30 days logistic
`5c3d2d4f058e4d23ab95592a8e5a1816`; 60 days logistic `4ba5bd8a41c24e2d8a31778105a20065`.

Current step, failing cells: logistic 17 vs LightGBM 6.

| Type | Cell | Logistic | LightGBM |
|---|---|---|---|
| Default-type (roll to 30) | vintage 2018 | 1.36 | pass |
| | late-history band 0 | 1.26 | pass |
| | late-history band 4-5 | 0.78 | pass |
| | 60 days late last month | pass | 1.28 |
| Payoff | vintages 2001 / 2007 / 2008 / 2018 / 2019 | 1.92 / 1.57 / 1.59 / 1.46 / 1.48 | 1.30 / 1.26 / 1.41 / 1.44 / 1.46 |
| | vintages 2002 / 2003 / 2005 / 2006 / 2010 / 2011 / 2012 / 2017 | 0.74 / 0.67 / 1.35 / 1.62 / 0.77 / 0.73 / 0.54 / 1.41 | pass |
| | 30 days late last month | 0.50 | pass |

**The roll-back gap is closed at one month** (2017-2019, predicted / actual):

| Move, by state last month | Before (no input) | After |
|---|---|---|
| 30 -> 60, coming down from 60 | 0.36 | **0.90** |
| current -> 30, just cured from 30 | 0.50 | **0.90** |
| current -> 30, just cured from 60 | 0.57 | **1.20** |
| current -> 30, current last month | 1.18 | 1.13 |

**The projection plumbing is verified.** For real loans that went 60 -> 30 -> current in early 2017,
the inputs the projection gives the models at each month ("state last month" and "months late in the
past 12") equal an independent count by hand over the 12-month window, and the projected relapse
chance equals the one-month model scored on the hand-built row (for example 0.28805 vs 0.28805 for
30 -> 60 after 60; 0.4307 vs 0.4307 for current -> 30 after a cure).

**2017-2019 projection** (Databricks MLflow run `e016b68856b5403cb4f4eb2494a4200c`, all-logistic; the
PC rerun reproduces it exactly). For information only, LightGBM at the current step with logistic at
30 and 60 (run `036a6310deaf466f80cbbd6ad284d531`; not a selection result):

| Starting state | Loans | Defaults actual | Refit (963d5a9) | **Retrain** | LightGBM-current (info) | Payoffs actual | **Retrain** | LightGBM-current (info) |
|---|---|---|---|---|---|---|---|---|
| Current | 275,952 | 2,377 | 2,365 (0.99) | **2,625 (1.10)** | 2,141 (0.90) | 83,337 | **63,851 (0.77)** | 68,786 (0.83) |
| 30 days | 2,040 | 529 | 424 (0.80) | **417 (0.79)** | 444 (0.84) | 422 | 397 (0.94) | 302 (0.72) |
| 60 days | 381 | 256 | 173 (0.67) | **182 (0.71)** | 185 (0.72) | 30 | 24 | 13 |
| All | 278,373 | 3,162 | 2,962 (0.94) | **3,225 (1.02)** | 2,770 (0.88) | 83,789 | **64,271 (0.77)** | 69,101 (0.82) |

The 1.02 total is **not** a pass in substance: current-start loans are over-projected (1.10) and
loans late at the start under-projected (0.79 / 0.71), and the two cancel. Failing cells: retrain 1
default cell (vintage 2016, 1.27) and 12 payoff cells; LightGBM-current 2 default cells (year 2018
0.77, vintage 2013 0.79) and 7 payoff cells. Read against Amendment 2 (a), a default cell outside the
bar on the test would fail it.

| Default cells by vintage (judged) | Actual | Refit | Retrain | LightGBM-current |
|---|---|---|---|---|
| 2012 | 234 | 1.04 | 1.23 | 1.07 |
| 2013 | 377 | 0.90 | 0.94 | 0.79 |
| 2014 | 405 | 0.98 | 1.07 | 0.87 |
| 2015 | 388 | 1.05 | 1.11 | 0.92 |
| 2016 | 357 | 1.22 | **1.27** | 1.08 |
| by year 2017 / 2018 / 2019 | 1,249 / 1,050 / 863 | 1.04 / 0.83 / 0.92 | 1.09 / 0.92 / 1.04 | 0.98 / 0.77 / 0.85 |

**Where the current-start over-projection comes from.** Scaling the current-loan model's payoffs up to
the actual total (information only): one factor (x 1.305) gives payoffs 78,883 (0.95) and defaults
2,526 (1.06); one factor per vintage gives 78,714 and 2,546 (1.07). So too few payoffs explain about a
third to 40% of the 248 extra defaults. The rest is concentrated in 2017 (896 projected vs 679 actual;
2018 0.95, 2019 1.10) and in young vintages (2014-2016: 1.17-1.32), where the current-loan model
over-predicts rolls to 30 days late.

**Vintage 2016.** Current at the start: 424 projected vs 321 actual (1.32); by year 2017: 121 vs 60,
2018: 145 vs 136, 2019: 158 vs 125. 30 days late: 23 vs 29; 60 days late: 6 vs 7. The miss is young
loans in their first projected year, as before the retrain (2017: 143 vs 80).

**Loans 60 days late at the start** (381 loans, 256 defaults):

| Months at 60 before Jan 2017 | Actual | Before | After |
|---|---|---|---|
| 1 | 171 | 120 (0.70) | 123 (0.72) |
| 2 | 43 | 28 (0.64) | 31 (0.73) |
| 3 | 15 | 7 (0.49) | 9 (0.57) |
| 4-6 | 21 | 11 (0.51) | 12 (0.58) |
| 7+ | 6 | 7 | 8 |

By months after the start: 1-3: 167 vs 154 (0.92); 4-6: 32 vs 12; 7-12: 31 vs 9; 13-36: 26 vs 8. Early
defaults are nearly right; the shortfall is defaults after month 3, after long or roundabout paths,
which a projection of probabilities over states (not individual paths) carries only roughly (L-026,
L-030).

**Loan age and housing turnover (current step, predicted / actual; 1999-2016 is a 1-in-10 sample of
the training months, in sample):**

| Loan age | Roll to 30, 1999-2016 | Roll to 30, 2017-2019 | Payoff, 1999-2016 | Payoff, 2017-2019 |
|---|---|---|---|---|
| 0-1 years | 0.96 | 1.24 | 1.01 | 1.44 |
| 2-3 | 1.02 | 1.10 | 1.00 | 1.04 |
| 4-6 | 1.02 | 1.02 | 0.99 | 0.76 |
| 7-10 | 1.03 | 1.03 | 0.95 | 0.87 |
| 11+ | 1.12 | 1.04 | 1.32 | 1.09 |

| Seasoned vintages (2002-2004, 2009-2013), payoffs by rate incentive | 1999-2016 | 2017-2019 | Share of the 2017-2019 miss |
|---|---|---|---|
| zero or below (no refinance incentive: home sales and moves) | 0.97 | 0.57 | 65% |
| 0 to 0.5 points | 0.91 | 0.63 | 31% |
| 0.5 to 1 | 0.97 | 0.86 | 11% |
| over 1 (refinancing) | 1.11 | 1.09 | (over-predicted) |

The pattern is not in the training years: it is drift in 2017-2019. A loan-age correction fitted on
1999-2016 comes out at about 1.0 and changes nothing in 2017-2019 (payoff vintage failures 13 -> 13;
roll-to-30 1 -> 3), so no fix passes the both-periods test without fitting on the validation years.
Recorded as **L-031**, with a candidate Phase 4 overlay (ADR-006, Q2), not sized now. For information:
LightGBM tracks the drift better (payoff vintage failures 5 vs 13), noted for the next annual review;
the selection rule is unchanged.

**Second start, January 2018 (information only; 294,224 loans, 24 months to December 2019, validation
months only; built with the pipeline's own SQL, only the start month changed; history tie-out 0
mismatches):**

| Starting state | Loans | Defaults actual | Projected | Payoffs actual | Projected |
|---|---|---|---|---|---|
| Current | 291,300 | 1,588 | 1,860 (1.17) | 60,178 | 49,671 (0.83) |
| 30 days | 2,518 | 450 | 437 (0.97) | 368 | 380 (1.03) |
| 60 days | 406 | 229 | 191 (0.83) | 24 | 23 |
| All | 294,224 | 2,267 | 2,488 (1.10) | 60,570 | 50,074 (0.83) |

Every judged default cell passes (by year 1.13 and 1.06; vintages 2013-2017 0.99-1.24); 11 payoff cells
fail (year 2018 0.79 and 10 vintages). Current-start loans by vintage group: 1999-2008 defaults 0.93,
payoffs 0.90; 2009-2015 defaults 1.22, payoffs 0.70; 2016-2017 defaults 1.25, payoffs 1.06. The same
picture as the 2017 start: young loans' defaults over-projected, seasoned loans' payoffs under-projected.

**Stopping rule (Martins, 18 Nov 2026):** this was the last round of fixes before the out-of-time test;
what remains is recorded in the limitations log (L-028, L-029, L-030, L-031). Martins's written
expectation for the test is committed beforehand: `docs/phase2/out_of_time_expectations.md`.

Databricks minutes for this round: feature build 14.2, nine trainings about 216, two projections 7.9
and about 8 (about 246 in all); the diagnoses ran on the PC and the SQL warehouse (a few minutes of
warehouse time to build the January 2018 start and a training sample).

### Out-of-time test, July 2022 - June 2026 (run once, 18-19 November 2026)

**Result statement (pre-registered wording, approved by Martins before the test; numbers filled from
the test run):**

> **Out-of-time test (July 2022 - June 2026), run on 18-19 November 2026: failed.** The locked models (logistic transition models
> for loans current, 30 and 60 days late; chosen on 2017-2019 under a rule written before the test)
> were scored once on four years they had never seen. 11 of
> 13 judged default cells were outside the 0.80-1.25 bar: years 2023 (0.57), 2024 (0.46), 2025 (0.46) and 2026 (0.36); vintages 2015 (0.55), 2016 (0.67), 2017 (0.42), 2018 (0.42), 2019 (0.44), 2021 (0.56) and 2022 (0.39).
> Projected defaults were 2,240 against 4,170 actual (0.54);
> payoffs 51,106 against 64,356 (0.79); 22 of
> 25 payoff cells were outside the bar (L-028). Loans 60 days late at the start:
> 0.86. What we expected beforehand,
> and how it compares: Martins expected too many defaults for loans current in July 2022 (10-20% over) and too few for loans 30 or 60 days late, with the two partly cancelling, and a possible failure of the vintage bar; the test failed as he thought it might, but in the other direction for current loans, which were projected at about half their actual defaults (1,887 vs 3,636, 0.52), so nothing cancelled; loans late at the start were under-projected as expected (30 days 0.58, 60 days 0.86). He expected payoffs to be over-predicted: at one month the current-loan model did over-predict them (1.21), but the projection under-projected them overall (0.79), most of all for seasoned vintages (0.20-0.61), while it over-projected the 2020-2022 vintages (1.48-1.79). The models were **not** tuned, swapped or re-chosen
> on the test: they stay `@challenger`, are not promoted to champion, and the failure is recorded in
> the limitations log (L-032). Any fix is a new model version, validated on a new period
> and approved by Martins; the reserve built in Phase 4 states this result and its effect next to
> every figure. Scenario: the economy as it actually happened from July 2022 to June 2026 (actual
> unemployment, house prices and mortgage rates), not a forecast. Known limitations that still apply:
> payoffs under-projected after the first year (L-028), recorded calibration cells (L-029), late
> defaults for loans 30 or 60 days late at the start (L-030) and loan-age and housing-turnover drift
> (L-031). Educational model on Freddie Mac conforming mortgages; not an audited bank reserve and not
> accounting advice. Test run `70104b1e40854feda47122e1f70c8dd1`, code `3943726`.

**Run details.** Databricks run 1040753991237338 (55.9 minutes), MLflow run
`70104b1e40854feda47122e1f70c8dd1`, code `3943726`, selection run `10fdda9c`, ADR-009 commit `e7b9ca7`.
Every launcher guard passed first (expectation final, statement approved, @challenger matches the locked
set, COVID sensitivity recorded, outside the blocked windows). A first launch (run 374483380470260)
stopped after 2 minutes at the start-table tie-out, before any model was loaded or anything was scored
or logged; the check was corrected (change log) and the test launched once more.

**Projection by starting state (1 July 2022, 48 months):**

| Starting state | Loans | Defaults actual | Projected | Payoffs actual | Projected | LightGBM-current (info): defaults |
|---|---|---|---|---|---|---|
| Current | 256,282 | 3,636 | 1,887 (0.52) | 63,972 | 50,855 (0.79) | 2,011 (0.55) |
| 30 days | 1,671 | 383 | 223 (0.58) | 345 | 239 (0.69) | 272 (0.71) |
| 60 days | 275 | 151 | 130 (0.86) | 39 | 12 | 134 (0.89) |
| All | 258,228 | 4,170 | 2,240 (0.54) | 64,356 | 51,106 (0.79) | 2,417 (0.58) |

By year, defaults projected / actual: 2022 (July-December) 1.07; 2023 0.57; 2024 0.46; 2025 0.46; 2026
(January-June) 0.36: the projection starts on target and falls further behind each year.

**Each transition on the test months (predicted / actual, whole period):**

| Step | Move | Locked logistic | LightGBM (info) | EBM (info) |
|---|---|---|---|---|
| Current | to 30 days late | 0.75 (by year 0.67-0.83) | 0.76 | 0.81 |
| Current | to paid off | 1.21 (by year 0.78-1.45) | 1.09 | 0.89 |
| Current | to 60 / default (small cells) | 1.11 / 1.71 | 0.97 / 0.89 | 1.43 / 2.61 |
| 30 days | to current / 60 / default / paid off | 1.11 / 0.94 / 1.16 / 1.10 | 1.16 / 0.89 / 0.92 / 1.34 | 1.13 / 0.97 / 1.14 / 0.88 |
| 60 days | to current / 30 / default | 1.05 / 1.26 / 1.08 | 1.10 / 1.35 / 1.12 | 1.06 / 1.19 / 1.09 |

Log loss: current 0.06599 (LightGBM 0.06240, EBM 0.06373); 30 days 0.95154 (0.95729, 0.95328); 60 days
1.46016 (1.51386, 1.47014). The 30- and 60-day models hold up at one month (most moves 0.89-1.16; the
60 -> paid-off move is still never predicted, L-027). The current-loan model under-predicts loans falling
behind by about a quarter in every year and every late-history band, and over-predicts payoffs at one
month, while the full projection under-projects payoffs (seasoned vintages 0.20-0.61, 2020-2022 vintages
1.48-1.79).

**COVID sensitivity (information only; run before the test; the locked set did not change).** The three
logistic models retrained at the locked code (faa0e3c) with January 2020 - June 2022 added to training
(MLflow runs `e0f62204`, `fdfb0688`, `7dff2b94`; 2017-2019 projection run `16a6bf67`; 23.5 Databricks
minutes):

| | Locked (without COVID months) | With COVID months |
|---|---|---|
| Log loss: current / 30 / 60 | 0.07545 / 0.90898 / 1.30648 | 0.07516 / 0.90824 / 1.26302 |
| Failing one-month cells: current / 30 / 60 | 17 / 2 / 0 | 15 / 6 / 0 |
| 2017-2019 projected defaults | 3,225 (1.02): current 1.10, 30 days 0.79, 60 days 0.71 | 2,828 (0.89): current 0.95, 30 days 0.72, 60 days 0.69 |
| Default cells outside the bar | 1 (vintage 2016, 1.27) | 0 (lowest: year 2018, 0.81) |
| 2017-2019 projected payoffs | 64,271 (0.77), 12 cells fail | 69,912 (0.83), 11 cells fail |

Adding the COVID months lowers projected defaults by about 12% and raises payoffs. Every 2017-2019 default
cell would pass, but it was not the locked set and was not tested out of time.

### Model v2: walk-forward exams failed at the crisis exam; work stopped before the holdout (19 November 2026)

Rules: ADR-009 Amendment 3 (accepted 19 November 2026, before any v2 training), plan
`docs/phase2/model_v2_plan.md`. The rate-lock inputs were identified from the version 1 out-of-time diagnosis. The July 2024 – June 2026 holdout was not used for training or model selection.

**What v2 is.** The same three steps (loans current, 30 and 60 days late), with 28 inputs: v1's 21 plus
rate lock (`locked_1`, `locked_2`, `locked_x_age`) and longer memory (`worst_state_6`,
`months_since_first_miss`, `paused_12`, `ever_missed`). Trained on 1999-2019 plus July 2022 - June 2024; the
COVID months (2020 - June 2022) are not fitted. Projection by path simulation (`cecl/models/simulate.py`,
8 paths per loan, fixed seed) instead of v1's slice projection (L-026). Logistic and LightGBM only (the EBM
is dropped for v2). Code `c160e46`.

**Household stress inputs: none kept.** Screened on the current-loan logistic model against the keep rule
(average F1-F4 log-loss gain at least 0.2%, no exam more than 0.1% worse, a Fed scenario path needed;
MLflow run `881837c1`): CPI inflation -0.065% average (worst exam +0.33%), real income growth -0.60% (worst
+1.9%), card delinquency +0.14% (no Fed path, so not eligible; information only).

**Selection (2% rule on the average F1-F4 log loss; record `docs/phase2/model_v2_selection.json`, MLflow
run `4e886684`):**

| Step | Logistic (C) | LightGBM (leaves, min weight) | LightGBM gain | Bars (logistic / LightGBM) | Winner |
|---|---|---|---|---|---|
| Current | 0.09506 (1.0) | 0.09216 (15, 200) | +3.05% | fail / fail | logistic |
| 30 days | 0.94888 (1.0) | 0.94881 (15, 200) | +0.01% | fail / fail | logistic |
| 60 days | 1.18578 (1.0) | 1.21373 (15, 50) | -2.36% | fail / fail | logistic |

At the current step LightGBM clears the 2% margin but fails calibration cells, so it does not qualify;
logistic is kept at every step with its failing cells recorded. Most failing cells are on the crisis exam
F1 (payoffs over-predicted 1.5-4x while refinancing froze) and payoffs on F2-F4 (L-028). The chosen and
the all-logistic combinations are therefore the same, so the combination check cannot change anything.
Training runs: current `d0e07d92`, 30 `995197d8`, 60 `aafd64ee` (LightGBM: `d4663583`, `985a16b3`,
`5f0eac08`).

**Walk-forward exams (36-month projections with the economy as it happened; Databricks run
648217473030703, MLflow run `5ccd125f`):**

| Exam | Trained to | Start | Loans | Defaults actual | Projected | Ratio | By year | Verdict |
|---|---|---|---|---|---|---|---|---|
| F1 crisis | 2007 | Jan 2008 | 223,495 | 14,756 | 5,552 | 0.38 | 0.59 / 0.33 / 0.30 | **fail**: v1-style benchmark 5,763 (0.39) is closer |
| F2 | 2010 | Jan 2011 | 241,427 | 10,150 | 9,303 | 0.92 | 0.92 / 0.95 / 0.86 | pass |
| F3 | 2013 | Jan 2014 | 232,310 | 4,071 | 4,439 | 1.09 | 1.07 / 1.11 / 1.09 | pass |
| F4 | 2016 | Jan 2017 | 278,373 | 3,162 | 3,449 | 1.09 | 1.12 / 1.06 / 1.08 | pass |
| F5 (information) | 2019 | Jul 2022 | 258,228 | 2,057 | 1,481 (24 months) | 0.72 | 1.00 / 0.69 / 0.58 | reported only |

Under Amendment 3 a walk-forward failure triggers the combination check; all-logistic is the same
combination and fails too, so **the failure is recorded and the work stops for Martins.** The holdout was
not run; nothing was tuned, swapped or re-chosen after the exams; v2 is not registered.

**What the crisis exam shows.** Both v2 and the v1-style benchmark (v1's inputs and logistic settings,
refitted on the same months, v1's projection) project about 38-39% of the 2008-2010 defaults: by starting
state, current loans 4,537 vs 13,049 (0.35), 30 days late 730 vs 1,273 (0.57), 60 days late 285 vs 434
(0.66); by vintage 2005-2007 at 0.26-0.42; payoffs 115,246 vs 86,706 (1.33). v2 is 1.4 points further from
actual than the benchmark (0.376 vs 0.391). A model trained only on 1999-2007 has never seen a national
house-price fall, so neither version can extrapolate to it; the exam was set as "no worse than v1", and
v2 is marginally worse, so it fails as written.

**Other exam detail (reported, not part of the pass rule for F2-F4).** Vintage default cells outside
0.80-1.25: F2 2002-2003 (0.74-0.77); F3 2011-2013 (1.32-1.43); F4 2012 (1.48) and 2016 (1.33). Payoffs:
F2 81,995 vs 125,898 (0.65), F3 0.97, F4 65,101 vs 83,789 (0.78), so the payoff under-projection after the
first year (L-028) remains. Start histories rebuilt from the panel matched every start row's inputs on all
five exams (0 mismatches); simulation noise (F4 all-logistic, seeds 7 and 8) 0.69% on total defaults.

**F5 (information; trained to 2019, so it has not seen the rate-lock period).** Defaults 1.00 in late
2022, then 0.69 (2023) and 0.58 (2024); current loans 0.73, 30 days 0.60, 60 days 0.92. v1's out-of-time
test from the same start (trained to 2016, a 48-month projection) gave 1.07, 0.57 and 0.46 in those years.
This is the limit stated in the plan: before 2022 the exams can show "no harm" but not that the rate-lock
fix works; only the final v2 fit sees July 2022 - June 2024.

Databricks minutes for v2: feature build 18.9, screening 17.0, six trainings 139.5 (current LightGBM
89.8), walk-forward 10.1: about 186 in all (plan estimate 400-450).

### ADR-009 Amendment 4: written after the crisis-exam result (19 November 2026)

**Timing, stated plainly: this amendment was written after the v2 walk-forward results were known,
including the failed crisis exam.** Amendments 1-3 were fixed before the results they govern; this one
was not. The crisis-exam failure stays on record exactly as reported above (L-035, MLflow `5ccd125f`).

- **Change:** the 2008-2010 crisis exam becomes information only. Reason (Martins's decision): a model
  trained on 1999-2007 has never seen a national house-price fall, so neither v2 (0.38) nor the v1-style
  benchmark (0.39) can project the crisis, and the "closer than v1" rule could not test what it was meant to.
- **Replacement test, before the holdout:** a downturn-sensitivity check on the final v2 fit. Projected
  defaults on the 1 July 2022 book (36 months, economy held at June 2022 levels as the baseline) must rise
  monotonically and materially as house prices fall 10/20/30% and unemployment rises 2/4 points. The
  thresholds (tests 1-7, ADR-009 Amendment 4) were approved by Martins and committed before any shock code was written.
- **If it passes:** v2 is registered in dev as `@challenger` and v1's alias is retired (v1's runs and its
  out-of-time failure, L-032, stay on record); then the holdout runs once under the unchanged Amendment 3
  rule E. **If it fails:** v2 is not registered, the holdout is not run, and the work stops for Martins.
- Every other Amendment 3 rule is unchanged, including the champion criteria.

### Model v2 downturn-sensitivity check (ADR-009 Amendment 4): failed on test 7 (19 November 2026)

Run once at code `9818c5f` on the thresholds approved and committed beforehand (`f0ded8c`): Databricks run
982744500972255 (6.8 minutes), MLflow run `69232827`. Final v2 fits (logistic; runs `d0e07d92`, `995197d8`,
`aafd64ee`), every loan alive on 1 July 2022 (258,228), 36 months, 8 paths, the same seed in every scenario,
the economy held at June 2022 levels as the baseline.

| Scenario | Projected defaults | R (vs baseline) |
|---|---|---|
| Baseline | 2,959 | 1.00 |
| House prices -10% / -20% / -30% | 3,411 / 4,155 / 5,273 | 1.15 / 1.40 / 1.78 |
| Unemployment +2 / +4 points | 3,676 / 4,251 | 1.24 / 1.44 |
| Combined (-30% and +4) | 6,970 | 2.36 |

| Test | Result |
|---|---|
| 1 Monotonic (each step at least +2%) | pass: steps +15%, +22%, +27% (house prices); +24%, +16% (unemployment) |
| 2 Material, house prices (1.10 at -20%, 1.25 at -30%) | pass: 1.40, 1.78 |
| 3 Material, unemployment (1.10 at +2, 1.25 at +4) | pass: 1.24, 1.44 |
| 4 Plausible (no R above 10) | pass: largest 2.36 |
| 5 Mechanics | pass: start histories 0 mismatches; shocked inputs differ from the baseline by exactly the shock (largest error 9e-16) |
| 6 Combined (at least 1.50 and above both single shocks) | pass: 2.36 vs 1.78 and 1.44 |
| 7 Risk ordering (R higher for the riskier group) | **fail** on 2 of 4 comparisons (below) |

| Test 7 comparison | Riskier group R | Safer group R | Result |
|---|---|---|---|
| House prices -30%: LTV above 80% vs at or below, after the shock | 2.56 (66,161 loans) | 1.39 (191,592) | pass |
| House prices -30%: credit score below 680 vs 740+ | 1.68 (24,817) | 1.78 (161,928) | **fail** |
| Unemployment +4: LTV above 80% vs at or below (no house-price change, so the 9,190 loans already above 80%) | 1.33 (9,190) | 1.45 (248,563) | **fail** |
| Unemployment +4: credit score below 680 vs 740+ | 1.46 | 1.43 | pass |

**Verdict under Amendment 4: failed.** v2 is not registered, v1's `@challenger` alias is not retired, the
holdout is not run, and the work stops for Martins (limitations log L-037).

**For the record, not a re-reading of the test:** test 7 compares *ratios* (R), as approved. In absolute
terms the riskier group's default rate rises more in every comparison, including the two that fail:
score below 680 under house prices -30%, 3.23% to 5.44% (+2.21 points) vs 740+ 0.59% to 1.05% (+0.46);
LTV above 80% under unemployment +4, 2.08% to 2.78% (+0.70) vs 0.80% or below 1.11% to 1.60% (+0.49). A
group that starts with a much higher default rate can rise more in points but less as a ratio. Whether
test 7 should have been written in points is Martins's decision; the result above stands as run.

Information only: by starting state, current loans R 1.88 (house prices -30%) and 1.47 (unemployment +4),
loans 30 days late 1.32 / 1.36, 60 days late 1.06 / 1.08 (most already default either way). By vintage,
house-price sensitivity rises with how recent the loan is (R at -30%: 2002-2014 about 0.9-1.2 on small
groups; 2017 1.45; 2019 1.68; 2020 2.02; 2021 2.63; 2022 2.77), as the newest loans have the least equity;
unemployment +4 is flatter (1.29-1.67). The 10 largest states: R at -30% 1.63 (NC) to 1.98 (IL); at +4
points 1.34 (CA, NY) to 1.58 (PA).

### ADR-009 Amendment 5: test 7 in percentage points; the holdout rules locked (19 November 2026)

**Timing, stated plainly: this amendment was written after the downturn-check result was known,
including the failure of test 7 as approved.** It is the second rule change for v2 made after a result
(Amendment 4, which replaced the crisis exam, was the first). The test-7 failure stays on record exactly as
reported above (L-037, MLflow `69232827`).

- **Change (Martins's decision):** the ratio R was the wrong measure for comparing groups with very different
  starting default rates; test 7 now requires the riskier group's **rise in projected default rate, in
  percentage points**, to exceed the safer group's.
- **No re-run:** the scenarios, models, book and projections are those of run `69232827`; test 7 was
  re-evaluated on its saved results (`scripts/reevaluate_downturn_v2.py`, MLflow run `eec4c05a`); tests 1-6
  keep their recorded results.
- **The lock:** this is the last change to v2's validation rules. The holdout rules (Amendment 3, E) are
  locked; the holdout runs once and its result stands whatever it shows.

**Test 7 re-evaluated (36-month projected default rate, same loans, baseline -> shock):**

| Shock | Comparison | Riskier group | Safer group | Result |
|---|---|---|---|---|
| House prices -30% | LTV above 80% vs at or below (after the shock) | 1.51% -> 3.87% (+2.36 points) | 1.01% -> 1.41% (+0.39) | pass |
| House prices -30% | Credit score below 680 vs 740+ | 3.23% -> 5.44% (+2.21) | 0.59% -> 1.05% (+0.46) | pass |
| Unemployment +4 | LTV above 80% vs at or below | 2.08% -> 2.78% (+0.70) | 1.11% -> 1.60% (+0.49) | pass |
| Unemployment +4 | Credit score below 680 vs 740+ | 3.23% -> 4.71% (+1.48) | 0.59% -> 0.84% (+0.25) | pass |

**Verdict under Amendment 5: the downturn-sensitivity check passes** (tests 1-6 as recorded; test 7 in
points). As Amendment 4 provides, v2 was registered in dev as `@challenger` (19 November 2026):
`cecl_lab_dev.ml.pd_v2_transition_current`, `_30`, `_60`, version 1 each (runs `d0e07d92`, `995197d8`,
`aafd64ee`; each version tagged with the check run and the re-evaluation run, "holdout: not yet run"). v1's
`@challenger` alias was removed from `pd_transition_current`, `_30`, `_60` (version 1 of each, tagged with
why); v1's model versions, runs and its out-of-time failure (L-032) stay on record. `@champion` is not set.
The holdout has not run; the holdout expectations file is left blank for Martins.

### Model v2 holdout, July 2024 - June 2026 (run once, 19 November 2026): failed

**Rules:** ADR-009 Amendment 3 E, locked by Amendment 5: every judged default cell (by year and vintage,
at least 200 actual defaults) and the total within 0.80-1.25. The result stands whatever it shows.
Martins's expectations were committed beforehand (`9c1cbae`, `docs/phase2/model_v2_holdout_expectations.md`).

**Run:** Databricks run 1036760341708514 (4.0 minutes), MLflow run `1ffe838e0047416d9e4e3d370375e00e`, code
`34963dd`, ADR-009 commit `7f6f19a`, v2 `@challenger` runs `d0e07d92` / `995197d8` / `aafd64ee`, downturn
check runs `69232827` and `eec4c05a`. Every launcher guard passed first. The 1 July 2024 start table, its
history and the outcomes were built inside the run; start histories matched every start row (0 mismatches)
and every start loan had an outcome row.

**Verdict: failed.** Total projected defaults 3,082 vs 3,814 actual (**0.81**, inside the bar), but **5 of 8
judged default cells are outside 0.80-1.25**:

| Cell | Actual | Projected | Ratio | Bar |
|---|---|---|---|---|
| Year 2024 (July-December) | 952 | 973 | 1.02 | pass |
| Year 2025 | 1,927 | 1,560 | 0.81 | pass |
| Year 2026 (January-June) | 935 | 549 | **0.59** | fail |
| Vintage 2019 | 221 | 165 | **0.75** | fail |
| Vintage 2021 | 384 | 351 | 0.91 | pass |
| Vintage 2022 | 884 | 569 | **0.64** | fail |
| Vintage 2023 | 885 | 556 | **0.63** | fail |
| Vintage 2024 | 345 | 243 | **0.71** | fail |

(Year 2025 and the total are inside the bar, by 0.01. Vintages with fewer than 200 actual defaults are reported, not judged.)

**By starting state:** current 313,666 loans, 2,805 actual vs 2,368 projected (0.84); 30 days late 2,995
loans, 679 vs 495 (0.73); 60 days late 487 loans, 330 vs 219 (0.66).

**Payoffs (reported; failures recorded under L-028):** 55,961 projected vs 52,990 actual (1.06); by year 2024
1.33, 2025 1.04, 2026 0.82; 8 judged payoff cells outside the bar: years 2024 (1.33), and seasoned vintages
2004-2005 (0.46-0.47), 2009-2012 (0.54-0.75) and 2016 (0.78). The 2020-2023 vintages are 1.08-1.22.

**Each transition on the holdout months (v2; v1's retired models for information):**

| Step | v2 log loss | v1 log loss | v2 AUC by move | v2 calibration (predicted / actual) |
|---|---|---|---|---|
| Current | 0.06969 | 0.07164 | to 30: 0.828; to default: 0.816; to paid off: 0.638 | to 30: 1.06; to paid off: 1.25; to 60 / default (small): 0.57 / 1.57 |
| 30 days | 0.95512 | 0.96771 | to 60: 0.606; to default: 0.768; to current: 0.726 | to current 1.07; 60 0.93; default 0.89; paid off 1.16 |
| 60 days | 1.29884 | 1.47226 | to default: 0.647; to current: 0.648 | to current 1.10; 30 1.13; default 0.97; paid off 0.46 |

v2's log loss is better than v1's at every step. One month at a time the models are close to calibrated
(the current-loan model's roll to 30 days late is 1.06, against v1's 0.75 on 2022-2026); the projection
nevertheless falls behind over time (defaults 1.02 in late 2024, 0.81 in 2025, 0.59 in 2026), as v1's did
(1.07 to 0.36), but less steeply, and the newest vintages are under-projected.

**Against Martins's written expectations:**

| Expectation (written before the run) | Result |
|---|---|
| Total defaults 0.90-1.10 | 0.81: lower than expected, inside the bar |
| Current loans 0.90-1.10 | 0.84 |
| 30 days late 0.80-0.95 | 0.73 |
| 60 days late 0.85-1.05 | 0.66 |
| At least one recent vintage above 1.25 (over-predicted) | The opposite: 2022-2024 vintages under-predicted (0.63-0.71) |
| Overall pass about a coin flip | Failed |
| AUC above 0.80 for current to 30 days late | 0.828 |
| AUC about 0.65-0.75 for 30 to 60 and 60 to default | 0.606 and 0.647 (just below the range) |
| Log loss better than v1 | Yes, at all three steps |
| Payoffs under-predicted after the first year (0.75-0.90), seasoned loans worst | Total 1.06 (2024 over, 1.33); 2026 0.82; seasoned vintages under (0.46-0.78), as expected |
| 2020-2021 vintages' payoffs better than v1 (v1 1.48-1.79), some cells outside | 1.21 and 1.08: better than v1; inside the bar |

**Measurement note, found after the run (L-039; the verdict stands):** the check counts projected chances only
up to each loan's actual exit, including a payoff or default. That counts survival twice and biases the projection
low. Counting up to censoring only, the holdout total is 0.92 (not 0.81) and the cells are years 1.09 / 0.92 /
0.74 and vintages 2019 / 2021 / 2022 / 2023 / 2024 at 0.84 / 1.02 / 0.74 / 0.76 / 0.80. v2 would still fail
(2026, 2022, 2023). The locked result is unchanged; the Phase 4 overlay is sized on the unbiased measure.

**What follows (ADR-009 Amendment 3 E, Amendment 5, Martins's expectations file):** the result stands; nothing
was tuned, swapped or re-chosen on it. v2 stays `@challenger` with the failure recorded (limitations log
L-038; registry versions tagged "holdout: failed"); it is not champion. Phase 4 uses a documented overlay
under ADR-006 for the default shortfall.

### Still to come

- The out-of-time test ran once on 18-19 November 2026 and **failed** (above); the models stay @challenger.
- Model v2 failed the walk-forward crisis exam (above); the work stopped for Martins's decision before the
  holdout. The v2 holdout expectations (`docs/phase2/model_v2_holdout_expectations.md`) stay blank.
- The v2 downturn-sensitivity check failed on test 7 (risk ordering, as ratios); v2 is not registered and the
  holdout has not run. Next step is Martins's decision (L-037).
- Under ADR-009 Amendment 5 (written after that result) the check passes with test 7 in points; v2 is
  `@challenger` in dev and v1's alias is retired. Next: Martins fills in the holdout expectations, then the
  holdout runs once under the locked rules.
- The v2 holdout ran once on 19 November 2026 and **failed** (total 0.81, 5 default cells outside the bar);
  v2 stays `@challenger`; Phase 4 uses a documented ADR-006 overlay (L-038).
- Registration of the @challenger, model card and model inventory entries (step M9).
