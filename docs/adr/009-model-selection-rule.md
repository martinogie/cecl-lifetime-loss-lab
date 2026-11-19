# ADR-009: Model-selection rule for PD and prepayment

- **Status:** accepted, 16 November 2026. Committed before any model is scored on the out-of-time
  test period.
- **Related:** roadmap v2 sections 4 and 5.1 and Phase 2 criteria; ADR-002 (definition of default);
  ADR-007 (sample weighting); `docs/phase2/plan.md`; model inventory M-01; Phase 6 (crisis and
  rolling backtests)

## Context

Phase 2 fits one model, the monthly competing-risks hazard (each month, the chance a loan defaults
and the chance it prepays, given it is still alive), in three families: a logistic scorecard-style
model (the bank-standard benchmark), LightGBM with monotonic constraints explained with SHAP, and
an Explainable Boosting Machine. Only one becomes the `@challenger` that Phase 4's reserve uses.

A selection rule written *after* seeing test results can be bent towards whichever model happened to
score best, which is exactly what model validators (SR 11-7) look for. So the rule is fixed here, in
advance, with the data split it depends on, and the out-of-time test is run once.

What the panel holds (Phase 1, sample files, 1999–June 2026): 73.7 million loan-months, 52,713
default events and 968,876 prepayments. Defaults by observation year are very uneven: about 1,000 a
year before 2007, 7,762 in 2009, under 900 in 2020–2021 (COVID forbearance stops the delinquency
clock, ADR-002), and 1,100–2,100 a year from 2023. Any split must leave enough defaults in each part
to judge calibration.

## Options

### A. The time-based split (by observation month, not by vintage)

Each loan-month belongs to the period it is observed in, so a loan can appear in training (its early
months) and in testing (its later months); what is never shared is calendar time. Features use only
information known at the start of the month.

| Option | Train | Validate (model choice) | Out-of-time test (once) | For | Against |
|---|---|---|---|---|---|
| A1 | 1999–2016 (39.5 million loan-months, 40,305 defaults) | 2017–2019 (10.9 million, 3,752 defaults) | July 2022 – June 2026 (14.9 million, 6,648 defaults) | Training includes the 2008 crisis; validation is a normal period; the test is the most recent four years; COVID kept out of all three | 2020 – June 2022 (8.4 million loan-months, 2,008 defaults) unused for fitting and choosing (studied separately in the Phase 6 forbearance test) |
| A2 | 1999–2016 | 2017–2019 | 2020 – June 2026 | Uses every month; the test includes the COVID period | The test score is dominated by forbearance effects the model isn't designed for |
| A3 | Expanding window: fit on years up to Y, score Y+1, for Y = 2012…2019; choose on the average | — (rolling) | July 2022 – June 2026 | Choice rests on many years, not one window; closest to the rolling backtest | Eight fits per family; more compute (PC time and, if used, Databricks minutes) |

The crisis backtest (train before 2007, test 2007–2010) is a separate refit in Phase 6, with the
same code and the family chosen here.

### B. What is measured on the validation period

| Measure | What it shows | Options for the bar |
|---|---|---|
| Log loss (default and prepayment) | How good the monthly probabilities are overall | B1 primary measure; B2 one of several |
| AUC by horizon (12, 24, 36 months, from a fixed start date) | Ranking power: risky loans above safe ones | Reported for every family; optional minimum |
| Calibration by year and by vintage: predicted ÷ actual defaults | Whether the levels are right (what the reserve depends on) | B3 every year and vintage with at least 200 defaults within 0.80–1.25; B4 within 0.90–1.10; B5 total only |
| Stability: PSI of the PD score between years | Whether the score drifts | Report; flag PSI above 0.25 |
| Explainability | Reasons for each loan's PD, in plain English | Pass/fail: reason codes must be producible for every loan |

### C. The rule that picks the winner

| Option | How | For | Against |
|---|---|---|---|
| C1. The benchmark stays unless beaten clearly | The logistic model is chosen unless a challenger passes the calibration bar *and* improves default log loss by at least a set margin (e.g. 2%) | Mirrors bank practice: complexity must earn its place; easiest to defend | May keep a slightly weaker model |
| C2. Best score that passes the bars | Among families passing calibration and explainability, the lowest default log loss wins | Simple, objective | Can pick a complex model for a tiny gain |
| C3. Weighted scorecard | Points for log loss, AUC, calibration, stability, explainability; highest total wins | Considers everything | The weights are themselves a judgment and are easy to argue about |

Ties (within the margin) go to the simpler family: logistic, then EBM, then LightGBM.

### D. Default and prepayment: one choice or two

| Option | For | Against |
|---|---|---|
| D1. One family for both causes, chosen on default performance (prepayment must still pass its calibration bar) | One model to document, validate and explain | Prepayment may be fitted less well |
| D2. Choose each cause separately | Each part as good as it can be | Two families to maintain; their probabilities must still be combined so default + prepayment never exceeds 1 |

### E. The out-of-time test protocol

- The test months are excluded from every table the models see until the test command runs.
- The test command refuses to run unless this ADR is accepted and committed, and records the commit
  it ran against.
- It runs **once**, for the chosen family (and, for information, the others), and its results are
  recorded whatever they show, in MLflow and the Phase 2 report.
- If the chosen model fails the calibration bar on the test, it is not tuned on the test: the
  failure is recorded in the limitations log, and any fix is a new model version validated on a
  new period, with Martins's approval.

## Consequences

- The split (A), the bars (B) and the rule (C, D) become fixed configuration, written into the
  training code and logged with every MLflow run.
- Every measure (log loss, AUC, calibration, PSI) is computed with the sampling weights (each sampled
  no-event month counts for the months it stands for), never on the raw sampled rows, so the
  measures describe the full panel.
- A sensitivity run refits the chosen family with the COVID period included in training
  (stop-the-clock applied, forbearance flag on) and is reported next to the main results; it is
  not used to choose.
- The Phase 2 report shows every family's validation results against the rule, and the single test
  run.
- Model validation (Phase 6) checks that the rule was followed.

## Decision

**Decision (Martins, 16 November 2026).**

**A:** train on 1999–2016 (including the 2007–2010 crisis), choose on 2017–2019, test once on July
2022 – June 2026; the COVID period (2020 – June 2022) is excluded from training and testing and
examined in the Phase 6 forbearance test, with a sensitivity run that includes it.

**B:** log loss is the main measure; calibration (predicted ÷ actual) must fall between 0.80 and
1.25 for every year and vintage with at least 200 defaults; AUC at 12, 24 and 36 months, score
stability and reason codes are reported.

**C1:** the logistic benchmark stays unless a challenger improves validation log loss by at least 2%,
passes every calibration bar and produces reason codes; ties go to the simpler model.

**D:** one model family covers both default and prepayment.

**E:** the out-of-time test runs once, its results are recorded whatever they show, and no model is
tuned on it.

*Why:* this mirrors bank practice: an explainable benchmark that a challenger must clearly beat,
calibration bars that allow for sampling noise but catch real bias, and a clean, untouched final
test.

## Amendment 1: the multi-state design (ADR-011)

- **Status of this amendment:** accepted, 17 November 2026 (with the tuning method added at
  approval). Locked before any training and before the out-of-time test.
- **Why:** ADR-011 (accepted 17 November 2026) replaces the single default/prepayment model with a
  monthly multi-state model. For each starting state (current, 30, 60) one model predicts next month's
  state; all three families (logistic, LightGBM, EBM) are fitted for every starting state, each only
  on the loan-months that start there: **3 transitions x 3 families = 9 models**. The original
  Decision above (split, bar, 2% rule, single test) is kept; this amendment says how it applies.

**A (split): unchanged.** Train on 1999-2016, choose on 2017-2019, test once on July 2022 - June 2026;
the COVID months (2020 - June 2022) stay out of fitting and choosing, with the sensitivity run.

**B (measures), for each transition model** (starting state current, 30 or 60), all weighted:
- **log loss over next month's states** (for example from 30: current, 30, 60, default, prepaid) on
  the 2017-2019 loan-months that start in that state: the main measure;
- **calibration bars per transition:** predicted ÷ actual counts of each move out of that state
  (for example current -> 30, current -> prepaid, 30 -> current, 30 -> 60, 60 -> default), by calendar
  year and by vintage, must fall within **0.80-1.25** in every cell with **at least 200 actual moves**
  of that kind; and for each move with at least 200 actual moves over the whole 2017-2019 period, the
  whole-period ratio must also fall within the bar (this catches a rare move, such as 60 -> default
  by vintage, whose cells are mostly under 200). Cells and moves below the minimum are reported, not
  judged. Why 200: the same minimum as the original B; with 200 events, sampling noise alone stays
  well inside 0.80-1.25 (about ±14% at two standard errors).
- AUC and Gini of each move out of that state, reported (not judged); training curves and the
  training-vs-validation fit table with its verdict, reported;
- **projection check on 2017-2019 (the chosen combination):** loans alive in January 2017, played
  forward month by month from their state that month with the three chosen transition models (the
  economy as it actually happened; each loan's later delinquency not known): projected vs actual
  defaults and prepayments by year and by vintage, against the same bar (cells with at least 200
  actual events). The all-logistic combination is projected too, as the benchmark.

**C (selection), the 2% rule applied per transition.** At each step (current, 30, 60) separately, the
logistic model stays unless LightGBM or the EBM **improves that transition's validation log loss by at
least 2%**, **passes that transition's calibration bars** and **produces reason codes** (top drivers of
the adverse moves: current -> 30, 30 -> 60, 60 -> default); ties go to the simpler family (logistic,
then EBM, then LightGBM). If both challengers qualify, the one with the lower log loss wins (the
simpler one if within 2% of each other). If the logistic model itself fails a bar at a step and no
challenger qualifies, logistic is kept and the failure is recorded, not hidden (the optional ADR-010
extras may then be tested with Martins's approval).

**Tuning (added at approval, 17 November 2026).** Each of the 9 models is tuned on the training
years only (1999-2016); the 2017-2019 validation years and the test months are never used for tuning.
- **Measure:** the step's own weighted log loss across **all its destinations** (for example from 30:
  cure, stay, roll to 60, default, prepay), not default log loss alone.
- **Walk-forward folds (three):** train to 2007 / score 2008-2010; train to 2010 / score 2011-2013;
  train to 2013 / score 2014-2016. The setting with the **best average log loss across the three
  folds** is chosen and refitted on all of 1999-2016; each fold's score is reported.
- **Grids:** logistic: regularisation C in {0.1, 1.0}; LightGBM: tree size (`num_leaves`) in {15, 31,
  63} x minimum leaf weight (`min_child_weight`) in {20, 50, 200} (9 settings; 400 rounds, learning
  rate 0.05 fixed); **EBM: default settings, not tuned** (its learning curve shows whether that holds
  it back).

**Combination check.** If the chosen combination fails a projection cell that the all-logistic
combination passes, the challengers are swapped back to logistic one step at a time, starting with the
step whose swap brings that cell closest to 1.0 (the step's effect, measured), until the combination
passes that cell or is all-logistic; every swap is recorded.

**D (mixed winners, how they are documented).** Different steps may end with different families.
This is recorded in:
- a **selection table** in the validation report: one row per step x family (log loss, % gain over
  logistic, each bar pass/fail, reason codes yes/no, fit verdict), the winner of each step and why;
- the **combination record**: the three chosen models with their family and MLflow run ID, logged
  together as one MLflow run (tags `transition_current`, `transition_30`, `transition_60`) and
  registered as one `@challenger` model whose wrapper holds the three; the projection check of the
  chosen and all-logistic combinations next to each other;
- the **model card** and **model inventory** (M-01): which family serves each step, the reason codes
  per step, and that the combination is validated as a whole by the projection check;
- any combination-check swap, with the projection cells before and after.

**E (the out-of-time test, once), what it measures:**
1. the **full multi-state projection** of the chosen combination from each loan's state on 1 July
   2022 to June 2026: projected vs actual **defaults and prepayments by vintage and by year**, against
   the 0.80-1.25 bar (cells with at least 200 actual events);
2. **each transition's calibration** (predicted ÷ actual moves by year and vintage, and whole
   period) on the test months;
3. log loss, AUC and Gini of each transition, reported.
It runs once, after this amendment is accepted and committed; its results are recorded whatever they
show; no model is chosen, swapped or tuned on it (the original E).

**Decision on this amendment (Martins, 17 November 2026):** ADR-009 Amendment 1: approved as written, including the combination check (keep it). Tune each step on its own log loss across all its destinations (for example from 30: cure, stay, roll to 60, default, prepay). Walk-forward tuning with three time folds (train to 2007 / test 2008–2010; train to 2010 / test 2011–2013; train to 2013 / test 2014–2016), choosing the setting with the best average across the folds and reporting each fold, plus the wider LightGBM grid (tree size plus minimum leaf weight). EBM stays on defaults; its learning curve will show whether that’s holding it back. The 2017–2019 validation years and the test months are never used for tuning.


## Amendment 2: a late-history calibration bar; the out-of-time verdict, payoffs, champion promotion

- **Status of this amendment:** accepted, 18 November 2026 (approved as drafted, with one sentence added
  to (d)). Accepted and committed **before**
  the out-of-time test runs; the test command refuses to run without it (original E).
- **Why:** after the 2017-2019 validation (validation report, Phase 2), four points were not written
  down anywhere: (1) what counts as an overall pass on the out-of-time test; (2) how payoff cells
  count, given the known payoff shortfall (limitations log L-028); (3) what a model needs to be
  promoted from `@challenger` to `@champion` (control C12 had a process but no criteria); (4) what
  happens to the shortfall in projected defaults for loans already late at the start. They are fixed
  here, before the test, so the test cannot shape its own pass mark.
- **And a new selection bar (added 18 November 2026).** The diagnosis below found a build mistake in
  the current-loan logistic model that **the existing bars could not catch**: they judge calibration
  by year, by vintage and over the whole period, and in each of those the model's over-prediction for
  loans late once and its under-prediction for chronic late payers cancel out. Only a split by
  late-payment history shows it. So every model now also has to pass calibration by late-history band.

**Context for (d): the late-loan shortfall, diagnosed (scoring only, 2017-2019).** In the
2017-2019 projection, loans 30 or 60 days late in January 2017 are projected at 0.68 and 0.66 of
their actual defaults; loans current at the start at 0.98. Scoring the 2017-2019 loan-months by
"months late in the past 12" shows why:

| Months late in the past 12 (loan current now) | Actual rolls to 30 | Predicted (logistic) | Ratio | LightGBM | EBM |
|---|---|---|---|---|---|
| 1 | 5,914 | 9,244 | 1.56 | 0.90 | 0.96 |
| 2 | 3,586 | 2,764 | 0.77 | 0.86 | 0.93 |
| 3 | 2,390 | 1,397 | 0.58 | 0.88 | 1.01 |
| 4-5 | 3,095 | 1,295 | 0.42 | 0.96 | 0.96 |
| 6-8 | 2,409 | 887 | 0.37 | 1.00 | 1.10 |
| 9-12 | 719 | 274 | 0.38 | 1.11 | 1.33 |

The current-loan logistic scorecard has a single cut for this input (0 vs 1 or more): its bins are
cut at weighted quantiles, and about 98% of current loan-months have the value 0, so the quantiles
collapse to one cut. It therefore gives a loan late once and a loan late ten times the same roll rate:
too high for the first, far too low for chronic payers. The errors cancel in the one-step totals
(which is why the one-step checks passed), but in the projection a loan that is late at the start
and cures goes back to the current-loan model and is scored as a one-time late payer, so its next
roll and default are under-projected. The 30- and 60-day models have 7-8 cuts for this input and are
calibrated within 0.95-1.16 in every band with real volume; LightGBM and the EBM handle it correctly.

It is fixable only by refitting the current-loan logistic model with whole-month cut points for this
input (0, 1, 2, 3, 5, 8), a retraining, then re-running the selection and the 2017-2019 projection
check. It cannot be fixed in the projection code. *(Secondary finding, same cause: credit score has no cuts at
all in the 30- and 60-day logistic models; both still calibrate by band, so it is recorded, not
fixed here.)*

**The new bar (B, added to Amendment 1 B).** For each transition model, the calibration of every move
out of that state (predicted / actual) must also fall within **0.80-1.25** in each **late-payment-history
band**: months late in the past 12 = **0, 1, 2-3, 4-5, 6+**, in every band with at least 200 actual
moves of that kind (bands below the minimum are reported, not judged). It applies at every step
(current, 30, 60), alongside the existing bars (by year, by vintage, whole period); the selection rule
(C) is otherwise unchanged: a challenger must pass every bar, including this one, and the logistic
model is kept, with its failures recorded, if no challenger qualifies. The selection at all three
steps is re-run under the amended bars on 2017-2019 only.

**Decision on this amendment (Martins, 18 November 2026):**

**(a) Overall verdict.** The out-of-time test passes when **every judged default cell** (by year and
by vintage, cells with at least 200 actual defaults, in the full multi-state projection from 1 July
2022) is inside **0.80-1.25**. Each transition's calibration, log loss, AUC and Gini are reported
with it (Amendment 1 E), but the verdict is on the projected defaults.

**(b) Payoffs.** Payoff cells are reported in full. A payoff cell outside 0.80-1.25 is recorded
under **L-028** and **does not fail the test**.

**(c) Champion promotion.** A model (or combination) is promoted from `@challenger` to `@champion`
only if it **passes the out-of-time test** under (a) **and** Martins has written a review approving
it; the promotion happens only in prod (control C12). A combination that fails (a) stays
`@challenger`; any fix is a new model version validated on a new period (original E).

**(d) The late-loan shortfall: fixed, not accepted.** The shortfall is not accepted as a known
limitation. The current-loan logistic model is refitted with whole-month cuts (0, 1, 2, 3, 5, 8) for
"months late in the past 12": this corrects a build mistake so the benchmark can compete fairly; it is
not a tuning on results. The late-history band bar (above) is added to the selection rule because
the old bars could not catch the mistake. The selection is re-run at all three steps under the
amended rule, on 2017-2019 only, and the 2017-2019 projection is re-run with the winners. The test
months are not touched.

**Results (2017-2019 only; recorded before acceptance).**

- **The refit** (commit 963d5a9; MLflow run `bc66cf67e80c4722ba8461791045c90b`; walk-forward tuning
  chose C = 0.1): all six whole-month cuts survive; the training roll rate to 30 rises from 0.35% a
  month (never late) to 29.5% (9+ late months). Log loss 0.07588 -> 0.07567. Calibration of
  current -> 30 by late-history band, before -> after: 0: 1.18 -> 1.21; 1: 1.56 -> 1.03; 2-3:
  0.70 -> 0.86; 4-5: 0.42 -> **0.73 (still fails)**; 6+: 0.37 -> 0.86. The scorecard adds each input's
  effect separately and cannot combine late history with other inputs; LightGBM and the EBM, which
  can, pass every band (0.87-1.15).
- **Selection under the amended bars, per step:**

| Step | Logistic | LightGBM | EBM | Winner and why |
|---|---|---|---|---|
| 60 days late | 1.32252; passes every bar, including the bands | 4.0% worse; 7 cells fail (3 by band) | 0.03% worse; passes every bar | **Logistic**: neither challenger is better |
| 30 days late | 0.92369; 2 cells fail (prepaid 2017; 30 -> 60 for 1 late month by band, 1.28) | 0.4% better; 5 fail | 0.7% better; 6 fail | **Logistic**: neither challenger is 2% better, and both fail bars |
| Current | refit 0.07567; 15 fail (13 payoff cells, 30 -> 60 vintage 2018, the 4-5 band) | 4.0% better; 6 fail (5 payoff cells by vintage, current -> 30 vintage 2003 at 0.76); passes every band | 3.6% better; 7 fail (mostly payoffs by vintage); passes every band | **Logistic (refit)**: both challengers are over 2% better and pass the new band bar, but still fail payoff calibration by vintage |

  Logistic is kept at every step; its own failures are recorded (the rule as written). The combination
  check has nothing to swap.
- **The 2017-2019 projection with the winners** (Databricks MLflow run `bd92a516887941f58dd574f5e2fb191f`,
  commit 963d5a9; the same projection rerun on the PC reproduces its totals): defaults 2,962 vs 3,162
  (0.94); every default cell passes (by year 1.04 / 0.83 / 0.92; vintages 2012-2016 0.90-1.22).
  Payoffs 67,291 vs 83,789 (0.80), the same 11 failing cells as before (L-028).

| Starting state (Jan 2017) | Actual defaults | Faded history (100e781) | Exact history (9548682) | Refit (963d5a9) |
|---|---|---|---|---|
| Current | 2,377 | 2,388 (1.00) | 2,335 (0.98) | 2,365 (0.99) |
| 30 days late | 529 | 355 (0.67) | 358 (0.68) | 424 (0.80) |
| 60 days late | 256 | 169 (0.66) | 169 (0.66) | 173 (0.67) |
| All | 3,162 | 2,912 (0.92) | 2,863 (0.91) | 2,962 (0.94) |

  The refit closes much of the gap for loans 30 days late at the start; loans 60 days late at the
  start are unchanged at 0.67, so their shortfall has another cause (not yet diagnosed). That
  remaining gap goes in the limitations log (L-030); it is not accepted as fixed.

Logistic's own recorded failures at the chosen steps (15 cells for the current-loan model, mostly payoffs, plus the 4-5 band for current -> 30; at 30 days late, prepaid 2017 and the 1-late-month band for 30 -> 60) are entered in the limitations log (L-029) and tracked in monitoring.

## Amendment 3: model v2

- **Status of this amendment:** **accepted**, 19 November 2026. Written, accepted and committed **before**
  any v2 training. Plan: `docs/phase2/model_v2_plan.md`.
- **Why:** model v1 failed the out-of-time test (L-032). Martins decided (19 November 2026) to build a
  second version (v2) before Phase 4. v1 keeps its record, its `@challenger` alias and Amendments 1-2.
  This amendment fixes v2's split, pass rule and selection rule in advance, so the results cannot shape
  their own pass marks.

**A (split).** Training: January 1999 - December 2019 and July 2022 - June 2024. January 2020 - June 2022
(COVID) is not fitted; it is used only to compute history inputs. Holdout: July 2024 - June 2026, run
once, after selection. Walk-forward
folds, each trained on all training months before its scored period: F1 to 2007 / 2008-2010; F2 to 2010 /
2011-2013; F3 to 2013 / 2014-2016; F4 to 2016 / 2017-2019. F5 (to 2019 / July 2022 - June 2024) is
reported only. Clean test: future Freddie Mac quarters (Phase 8).

**B (measures), per transition model.** The main measure is weighted log loss over next month's states,
averaged over F1-F4, with each fold reported. Calibration bars: 0.80-1.25 in every cell with at least 200
actual moves, on each fold's scored months (by year, by vintage, whole period and late-history band, as
in Amendments 1 and 2). New: a rate-lock band bar (incentive 2+ points below market, 1-2 below, 0-1
below, 0-1 above, more than 1 above), judged in-sample on July 2022 - June 2024 and labelled in-sample.
AUC, Gini, training curves and the fit table are reported.

**Household-stress inputs.** Only candidates with a path in the Fed's 2026 scenarios are eligible. Each
is added one at a time to the current-loan logistic model and kept only if it does all three of:
- improves the average F1-F4 log loss by at least 0.2%;
- worsens no fold by more than 0.1%;
- has a sign with a business reason.

**Families.** v2 fits two families per step: logistic and LightGBM (2 x 3 = 6 models). The EBM is not
used in v2 (not in the main fit, the walk-forward exams or the holdout); v1's records keep it.

**C (selection).** v1's grids for logistic and LightGBM, the 2% rule per step on the average F1-F4 log
loss, every bar passed and reason codes required, ties to the simpler family (logistic, then LightGBM),
and the combination check of Amendment 1.

**Walk-forward requirement (the chosen combination).** Each fold's fitted models are projected 36
months from the start of the fold's scored period.
- F2, F3 and F4: total projected defaults within 0.80-1.25, and every default cell by year with at least
  200 actual defaults within 0.80-1.25.
- F1: the total must be closer to 1.0 than the v1-input logistic benchmark on the same fold.
- On a failure, the combination check applies. If all-logistic also fails, the failure is recorded and
  the work stops for Martins.

**E (holdout pass).** v2 passes the holdout only if both hold in the full projection from 1 July 2024:
- every judged default cell (by year and by vintage, with at least 200 actual defaults) is within
  0.80-1.25;
- total projected defaults are within 0.80-1.25.

Payoff cells are reported; those outside the bar are recorded under L-028 and do not fail. Transition
calibration, log loss, AUC and Gini are reported. The holdout runs once, and nothing is chosen, swapped
or tuned on it. Any relaunch needs Martins's approval.

**Promotion.**
- A holdout pass: v2 may be used in Phase 4 as `@challenger`, labelled "holdout passed".
- `@champion`: needs Martins's written review and the first clean Freddie quarter with total defaults
  within 0.80-1.25.
- A fail: v2 stays `@challenger` and the result is recorded.

**Decision on this amendment:**

"Decision (Martins Ogiekpolor, 19 November 2026): Model v2 is selected and judged under the rules in this amendment, fixed before any v2 training. Each historical exam (2011–2013, 2014–2016, 2017–2019) must have total defaults and each year within 0.80–1.25. The 2008–2010 crisis exam passes if v2 is closer to actual defaults than the v1-style benchmark. The holdout (July 2024 – June 2026) passes only if every judged default cell and the total are within 0.80–1.25. The holdout runs once; any relaunch needs my approval. Promotion to @champion requires the holdout pass, my written review and one clean future Freddie quarter."

"EBM is dropped for v2 to cut training time; it never won a step in v1 and logistic already provides the explainable benchmark."
