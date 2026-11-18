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

- **Status of this amendment:** proposed, 17 November 2026 (pending Martins's approval). The
  out-of-time test command refuses to run while any amendment here is pending.
- **Why:** ADR-011 (accepted 17 November 2026) replaces the single default/prepayment model with a
  monthly multi-state model: logistic models for the moves out of current, 30 and 60 days late, with
  LightGBM and the EBM as challengers only on the move out of current. The original Decision above
  (split, bars, the 2% rule, the single test) is kept; this amendment says how it applies.

**A (split): unchanged.** Train on 1999-2016, choose on 2017-2019, test once on July 2022 - June 2026;
the COVID months (2020 - June 2022) stay out of fitting and choosing, with the sensitivity run.

**B (measures), per transition model** (one model per starting state: current, 30, 60), all weighted:
- log loss over the next-month states;
- **calibration bars per transition:** predicted ÷ actual counts of each move (for example current →
  30, 30 → current, 60 → default, any state → prepaid), by calendar year and by vintage, must fall
  within **0.80-1.25** in every cell with **at least 200 actual moves** of that kind (the same bar and
  minimum as the original B; cells with fewer moves are reported, not judged);
- AUC and Gini of the key moves, reported (not judged): current → 30, 30 → current, 60 → default;
- **projection check on 2017-2019:** starting from each loan's state in January 2017, the full
  multi-state projection (the economy as it actually happened, each loan's later delinquency not
  known) gives projected vs actual defaults and prepayments by year and by vintage, against the same
  0.80-1.25 bar (cells with at least 200 actual events). This is what the reserve depends on.

**C (selection), the 2% rule applies to one comparison only: the model for moves out of current.**
The logistic model stays unless LightGBM or the EBM (fitted for the same moves out of current)
improves validation log loss **of the current → 30 move** by at least 2%, passes every calibration
bar of the moves out of current and the projection check, and produces reason codes; ties go to the
simpler family. The models for moves out of 30 and 60 are logistic only: they must pass their own
calibration bars; if a bar fails, that is recorded (and the optional ADR-010 extras may be tested
with Martins's approval), not hidden.

**D:** the chosen family is used for moves out of current; logistic for moves out of 30 and 60. Each
family's model covers every move out of its starting state, prepayment included.

**E (the out-of-time test, once), what it measures:**
1. the **full multi-state projection** from each loan's state on 1 July 2022 to June 2026: projected
   vs actual **defaults and prepayments by vintage and by year**, against the 0.80-1.25 bar (cells
   with at least 200 actual events);
2. **each transition's calibration** (predicted ÷ actual moves by year and vintage) on the test months;
3. AUC and Gini of current → 30 (and of the other key moves), reported.
It runs once, after this amendment is accepted and committed; its results are recorded whatever they
show; no model is tuned on it (the original E).

**Decision on this amendment:** *pending, Martins.*
