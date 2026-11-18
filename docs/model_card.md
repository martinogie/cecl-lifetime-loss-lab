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
