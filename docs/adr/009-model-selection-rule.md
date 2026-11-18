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
