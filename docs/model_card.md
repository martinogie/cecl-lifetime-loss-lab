# Model card: CECL Lifetime Loss Lab

Draft, built up phase by phase. Educational model on Freddie Mac conforming mortgages; not an
audited bank reserve and not accounting advice.

## M-01 Lifetime PD and prepayment (Phase 2, in development)

- **What it predicts:** for each loan-month, the probability that the loan **defaults** (ADR-002:
  90+ days late or a loss event, stop-the-clock for flagged payment pauses) and that it **prepays**,
  given it is still alive: a discrete-time competing-risks hazard. P(no event) = 1 - P(default) -
  P(prepayment).
- **Data:** Freddie Mac sample files, 1999 - June 2026 (Phase 1 panel). Training months 1999-2016,
  validation 2017-2019, COVID months (2020 - June 2022) left out of fitting and choosing (sensitivity
  run only), test July 2022 - June 2026 scored once (ADR-009). Months with no event are sampled 1 in
  25 and weighted; every measure uses the weights.
- **Inputs:** 19 features known at the start of the month (`cecl/features.py`), plus, after ADR-010,
  an origination-era feature.
- **Three families** (ADR-009 chooses one; the logistic model is the benchmark):

| Family | Structure |
|---|---|
| Logistic scorecard | **One** multinomial logistic regression (no event / default / prepayment) on scorecard bins |
| LightGBM | **Two separate models**, one for default (default vs not) and one for prepayment (prepayment vs not), each with its own monotonic constraints; their probabilities are then **combined** as competing risks: P(no event) = 1 - P(default) - P(prepayment), capped so the sum never exceeds 1 |
| Explainable Boosting Machine | **Two separate models**, default and prepayment, combined the same way |

  Why two models for LightGBM and the EBM: LightGBM applies one monotonic constraint to every class
  of a multiclass model, but the expected directions differ by cause (higher LTV, more default;
  higher rate incentive, more prepayment). The monthly probabilities are small, so combining them
  this way loses nothing in practice; ADR-009 D ("one family covers both causes") is about the family.
- **Early stopping and tuning:** tuning uses one inner time split (fit 1999-2013, judge 2014-2016),
  then refits on all training years. LightGBM runs a fixed 400 rounds (the per-round validation curve
  is recorded, not used to stop). The EBM's internal early stopping holds out a random 15% of the
  training rows per bag (InterpretML's default; L-025).
- **Known limitations so far:** L-016 (forbearance flags only from 2014), L-018, L-023, L-024 (DTI
  missing for relief refinances; relief refinance is a feature), L-025, and the default-level shift
  between the training and validation years (ADR-010).

## M-01 version 2 (ADR-009 Amendment 3): stopped after the walk-forward exams, 19 November 2026

- **What changed from version 1:** the same three transition steps (current, 30 and 60 days late) with
  28 inputs: version 1's 21 plus rate lock (`locked_1`, `locked_2`, `locked_x_age`) and longer memory
  (`worst_state_6`, `months_since_first_miss`, `paused_12`, `ever_missed`); trained on 1999-2019 and July
  2022 - June 2024 (COVID months not fitted); projected by path simulation (8 paths per loan) instead of
  the slice projection; families logistic and LightGBM only (the EBM is dropped for v2). No household-stress
  input passed the keep rule.
- The rate-lock inputs were identified from the version 1 out-of-time diagnosis. The July 2024 – June 2026 holdout was not used for training or model selection.
- **Selected:** logistic at all three steps (LightGBM was 3.05% better at the current step but failed
  calibration cells). Code `c160e46`; selection record `docs/phase2/model_v2_selection.json`.
- **Walk-forward exams:** 2011-2013 (0.92), 2014-2016 (1.09) and 2017-2019 (1.09) pass on total defaults and
  every year. The 2008-2010 crisis exam fails: v2 projected 0.38 of actual defaults against 0.39 for the
  v1-style benchmark, so it is not closer. 2022-2024 (information, trained to 2019): 0.72.
- **ADR-009 Amendment 4 (19 November 2026), written after the crisis-exam result:** the crisis exam is now
  information only (the failure stays on record, L-035); in its place the final v2 fit must pass a
  downturn-sensitivity check (house prices -10/-20/-30%, unemployment +2/+4 points, the two together, and
  risk ordering; thresholds approved 19 November 2026 before any shock code was written). If it passes, v2 becomes `@challenger` in dev and v1's alias is
  retired; the holdout then runs once.
- **Downturn-sensitivity check (run 19 November 2026, MLflow `69232827`): failed on test 7.** Defaults rise
  monotonically and materially (house prices -30%: 1.78x; unemployment +4: 1.44x; both: 2.36x) and tests 1-6
  pass, but two risk-ordering comparisons fail as ratios (credit score below 680 vs 740+ under house prices
  -30%: 1.68 vs 1.78; LTV above 80% vs at or below under unemployment +4: 1.33 vs 1.45), although in points the
  riskier group rises more in both (L-037).
- **ADR-009 Amendment 5 (19 November 2026), written after the downturn-check result:** test 7 is measured
  as the rise in default rate in percentage points; re-evaluated on the saved check (no re-run), all four
  comparisons pass, so the check passes. **This is the last change to v2's validation rules: the holdout
  rules are locked and its result stands whatever it shows.**
- **Holdout (July 2024 - June 2026, run once 19 November 2026, MLflow `1ffe838e`): failed.** Total defaults
  0.81 (inside the bar) but 5 of 8 judged default cells outside 0.80-1.25: year 2026 0.59; vintages 2019 0.75,
  2022 0.64, 2023 0.63, 2024 0.71. By starting state: current 0.84, 30 days 0.73, 60 days 0.66. Payoffs 1.06
  (8 cells outside, L-028). Log loss better than v1 at every step; the current-loan roll to 30 days late is
  1.06 at one month, but the projection falls behind each year (1.02, 0.81, 0.59).
- **Status:** `@challenger` in dev (19 November 2026), **holdout failed**; not champion. v1's alias retired
  (v1's runs and its out-of-time failure, L-032, stay on record). Phase 4 uses a documented ADR-006 overlay for
  the default shortfall (L-038). Two of v2's validation rules were changed after their results (Amendments 4
  and 5); the original failures stay on record (L-035, L-037). Other limitations: L-028 (payoffs), L-036
  (household-stress inputs).
- Full results: validation report, "Model v2". Educational model on Freddie Mac conforming mortgages; not
  an audited bank reserve.
