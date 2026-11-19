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

### Still to come

- The out-of-time test (July 2022 - June 2026), scored once, only after Martins approves it.
- Registration of the @challenger, model card and model inventory entries (step M9).
