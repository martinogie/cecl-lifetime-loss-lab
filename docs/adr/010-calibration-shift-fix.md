# ADR-010: Fixing the default-level shift between the training and validation years

- **Status:** accepted 17 November 2026 (Option E), **amended the same day: the decision moves to ADR-011** (lifetime projection design); the era feature and level calibration remain optional extras.
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

**Diagnostics** (17 November 2026, existing models, scoring only, no retraining, never the test
months; MLflow run tagged `diagnostics`, job run at commit `175d1d2`, 2.2 minutes; training rows
scored on a sample: every default, 1 in 5 of the rest, re-weighted). Ratios are predicted / actual
defaults (weighted); AUC with Gini (= 2 x AUC - 1) in brackets.

*1. Delinquency at the start of the month (validation 2017-2019).* **98.5% of validation defaults
(3,695 of 3,752) come from loans already behind last month**; loans current last month produced 57.
So the 0.99 default AUC mostly separates current from delinquent loans.

| Family | Current loans: AUC (Gini) | Current: ratio | Delinquent loans: AUC (Gini) | Delinquent: ratio |
|---|---|---|---|---|
| Logistic | 0.850 (0.699) | 3.99 | 0.507 (0.014) | 1.44 |
| LightGBM | 0.605 (0.211) | 0.003 | 0.942 (0.883) | 1.43 |
| EBM | 0.820 (0.639) | 2.72 | 0.950 (0.901) | 1.24 |

The 57 current-loan defaults are too few for the ADR-009 bar (200), so their ratios are unstable;
the over-prediction that matters is among **delinquent** loans (1.24-1.44), which hold almost every
default: in 2017-2019, delinquent loans rolled into default less often than in 1999-2016.

*2. Predicted / actual defaults by calendar year.* Inside the training years the models are roughly
level: LightGBM and the EBM are within 0.81-1.15 in every year from 2003 to 2016 (no systematic
over-prediction in 2012-2016; 2016 slightly under, 0.81-0.85); the logistic model is 0.91-1.18
(2008 under, 0.72; 2015-2016 over, 1.17-1.18). The gap opens **after** the training window:
2017 1.16-1.34, 2018 1.36-1.48, 2019 1.27-1.74.

*3. Prepayments by calendar year.* Training years swing widely: under-predicted in the 2003 refinance
boom (0.56-0.70) and over-predicted in 2014-2016 (1.12-1.36); validation 0.90-1.26. The level is
roughly right on average, the timing of refinance waves is not.

*4. 2017-2018 in TX, FL and PR (hurricane states, L-016) vs the rest.* Hurricane states: 1.56-1.73
(356 defaults); other states: 1.20-1.36 (2,112 defaults). Disaster forbearance adds to the gap in
those states, but they hold 14% of the defaults: most of the over-prediction is nationwide.

*What this means for the era explanation (Martins's Decision asks to be told before retraining if
the diagnostics contradict it):* the evidence **only partly supports** it. For underwriting quality to
explain the gap, the post-2009 vintages would have to be over-predicted where they were seen, but in
2012-2016 the tree models were level. The gap is concentrated among loans already delinquent, and it
appears after 2016 across the country: that points more to fewer delinquent loans rolling into
default (stronger cures, loss mitigation and rising house prices) than to origination-era quality
alone. The era feature may still help (post-2009 loans that fall behind may cure more), but it is not
the clear cause the Decision assumed.

**Also found (affects every family, and Phase 4):** the 12/24/36-month AUC (`metrics.horizon_auc`) uses
each loan's *actual* later months, including its later delinquency status, so it overstates how well
a lifetime forecast made at the start date would rank loans. And because the strongest inputs are
last month's delinquency, a lifetime projection from a reserve date (when most loans are current)
needs either a delinquency-transition model or a PD that does not depend on future delinquency.

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

**Amendment (Martins, 17 November 2026).** The diagnostics only partly support the era explanation: there is no over-prediction of 2012-2016 within the training years, and the gap is nationwide, after 2016, and in already-delinquent loans, pointing to higher cure rates. This decision therefore moves to the ADR-011 design (lifetime projection). The origination-era feature and the 2014-2016 level calibration stay as optional extras, tested only if the evidence supports them. All training is paused until ADR-011 is decided.
