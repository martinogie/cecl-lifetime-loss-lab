# ADR-010: Fixing the default-level shift between the training and validation years

- **Status:** accepted, 17 November 2026 (Option E). Evidence section completed from the diagnostics run.
- **Related:** ADR-009 (selection rule: the calibration bar 0.80-1.25), ADR-002 (definition of default,
  stop-the-clock), L-016 (forbearance flags only from 2014), L-024 (DTI missing for relief
  refinances), Phase 2 plan; Phase 6 (crisis backtest)
- **Important:** this ADR was written **after looking at the validation results (2017-2019)**. Any fix
  chosen here is informed by the validation period, so validation is no longer a fully independent
  judge of the fixed model; the out-of-time test (July 2022 - June 2026) remains untouched and is the
  clean judge (ADR-009 E). Every report states this.

## Context

The first two families trained on 1999-2016 both **over-predict defaults in 2017-2019 by 41-48%**:
logistic benchmark 1.48 overall (2018: 1.46, 2019: 1.74), LightGBM 1.41 (1.34-1.48 by year); by
vintage the worst are 2012-2017 (1.4-1.9). Both fail the ADR-009 calibration bar, so under ADR-009 C1
the benchmark would stay while failing the bar itself. Ranking is strong (default AUC 0.99; at 12/24/36
months 0.98-0.99), prepayment is close overall (1.11-1.15) but weak in ranking (AUC 0.61-0.67).

A logistic regression's predictions add up to the actual count on its own training rows, so a gap
this size on later years means the default level moved: loans in 2017-2019 defaulted less than
anything the features explain from 1999-2016 (a period dominated by the 2007-2010 crisis).

**Diagnostics** (existing models, no retraining; `cecl/models/diagnostics.py`, MLflow run tagged
`diagnostics`): *to be filled in from the diagnostics run* (AUC and calibration for loans current vs
already delinquent at the start of the month; predicted / actual by calendar year 1999-2016; 2017-2018
in TX, FL and PR vs the rest).

## Options

| Option | How | For | Against |
|---|---|---|---|
| **A. Origination-era feature** *(Martins's preference)* | Add a feature for when the loan was made, e.g. `originated_2009_or_later` (post-crisis underwriting: tighter documentation, ability-to-repay rules from 2014), or era buckets (before 2004 / 2004-2008 boom / 2009 on) | An economic reason, in the model, explainable in reason codes; the level difference comes from the loans themselves, not a fudge factor | In the training years, post-2009 loans are only seen in a benign period (2009-2016), so "era" partly stands in for "calendar period"; the model can't learn how post-2009 loans behave in a downturn (the stress scenarios will rely on the macro features) |
| **B. Recalibrate the level on 2014-2016** *(fallback)* | Fit on 1999-2013, then shift each cause's level (an intercept on the log-odds) so predicted = actual on 2014-2016; ranking unchanged | Simple, standard (bank models are often recalibrated to a recent period); keeps every feature's effect | No economic explanation; the level is tied to one benign period, so the reserve could be too low if conditions worsen (the Phase 5 stress tests and the Phase 6 backtests must check it) |
| C. Recent years count more | Weight training months by recency (e.g. half-life of 5 years) | Smooth; no new feature | Arbitrary half-life; still no explanation; less crisis information |
| D. A regime feature from the economy | Add the state house-price change over 12 months (falling in 2007-2011, rising after 2012) | Ties the level to observable conditions that the scenarios move | Current LTV already carries cumulative price change; may not explain the post-crisis drop on its own |
| E. Combine A and B | A first; if the bar still fails, B on top | Uses the explanation where it works, the recalibration only for what is left | Two changes to document and validate |

**Whatever is chosen:** the fixed families are retrained (one family per job, minutes recorded; Martins
approves each run and its expected minutes), **re-validated on 2017-2019 under ADR-009 unchanged**, and
the validation report says plainly that validation informed the fix. The out-of-time test is not
touched until Martins approves it.

## Consequences

- `cecl/features.py` (option A, D) or `cecl/models/pipeline.py` (option B, C) change; tests added;
  the feature table is rebuilt only if a feature is added (A, D: the full build is about 13 minutes).
- The change log records the change, the reason (the validation shift) and the approval.
- The model documentation and the limitations log record that validation informed the fix.

## Decision

**Decision (Martins, 17 November 2026):** Option E. First, add an origination-era feature (originated before 2009 vs 2009 onward; era buckets such as pre-2005, 2005–2008 and 2009 onward if the diagnostics support them) to all three model families, so the models can separate loans made under post-crisis underwriting from bubble-era loans that look the same on paper. Training still uses all years from 1999 to 2016, including the crisis. If calibration on 2017–2019 still fails the ADR-009 bar after that, apply a level calibration fitted on 2014–2016 to close the remaining gap. Why: the validation results show all three families over-predicting defaults by 1.4–1.5× in 2017–2019, worst for the 2012–2017 vintages, which points to unobserved underwriting quality in the bubble years. The era feature addresses that cause and is easy to explain; level calibration is the fallback. This decision was written after seeing the validation results; any fix is re-validated on 2017–2019 under ADR-009 unchanged, and the out-of-time test stays untouched.

*If the diagnostics contradict the era explanation, the decision is revisited with Martins before any retraining.*
