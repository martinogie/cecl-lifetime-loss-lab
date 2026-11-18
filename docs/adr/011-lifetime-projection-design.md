# ADR-011: Lifetime projection design (how the PD model produces lifetime losses)

- **Status:** proposed, 17 November 2026 (Decision pending: Martins). All model training is paused
  until this is decided.
- **Related:** ADR-002 (definition of default: 90+ days late or a loss event, first default only,
  absorbing; stop-the-clock for flagged payment pauses; modification while 60+ days late counts as
  default), ADR-003 (2-year forecast, 1-year reversion), ADR-005 (scenario weighting), ADR-008 (state
  scenario paths), ADR-009 (selection rule), ADR-010 (default-level shift; its decision moves here),
  Phase 4 (engine), Phase 5 (stress), L-016, L-025

## Context

Phase 2's first models predict, for each loan-month, the chance of default and of prepayment in the
next month, given everything known at the start of that month. The diagnostics (ADR-010) showed:

- **98.5% of 2017-2019 defaults came from loans already behind last month** (3,695 of 3,752); loans
  current last month produced 57. The 0.99 AUC mostly separates current from delinquent loans: by
  ADR-002's definition a current loan cannot reach 90 days late next month, only a loan already 60+
  days behind can.
- **The over-prediction in 2017-2019 (1.2-1.5x) sits among delinquent loans**, appears nationwide and
  only after 2016: fewer delinquent loans rolled into default than in 1999-2016, pointing to higher
  **cure rates** (loss mitigation, rising house prices), not mainly to origination-era quality.
- **Lifetime projection needs future delinquency.** CECL needs each loan's lifetime loss from the
  reserve date (30 June 2026), when most loans are current. A next-month model whose strongest inputs
  are last month's delinquency can't be run forward without knowing how delinquency evolves; holding
  delinquency at "current" for every future month would give almost no default for current loans
  (LightGBM's current-loan predictions were 0.3% of actual). The horizon AUC has been rebuilt to use
  start-date information only, which makes this visible (`pipeline.horizon_from_start`).

So the question is how Phase 2 should model default over a loan's life. Three designs:

## Options

### (a) Delinquency-transition (multi-state) model *(Martins's stated preference)*

Each month a loan is in one state: **current, 30, 60, 90+ (default)**, or **prepaid**. Default (90+,
or a loss event, ADR-002) and prepayment are absorbing. From each live state one model predicts next
month's state:

| From | To (next month) |
|---|---|
| current | current, 30, prepaid (a direct default only through a loss event) |
| 30 | current (cure), 30, 60, prepaid |
| 60 | current (cure), 30, 60, 90+ default, prepaid |

The engine multiplies the monthly transition matrices forward from each loan's state on the reserve
date, giving the chance of being in default, prepaid or still alive in every future month; with LGD
and EAD (Phase 3) that gives lifetime expected loss.

- **Phase 4:** lifetime PD from any starting state, month by month; the forecast-then-reversion of
  ADR-003 moves the macro inputs each month; current loans get real default risk (through 30 and 60).
- **Stress (Phase 5):** the scenario paths change each transition separately: unemployment and
  house prices push current -> 30 up and cures down; the severe scenario's effect on losses is
  traceable to which transition moved.
- **Macro links / cure rates after 2016:** cures (30 -> current, 60 -> current) get their own models
  with macro inputs (house-price change, unemployment), so the post-2016 rise in cures is modelled
  where it happens instead of distorting a single default model. The forbearance flag and
  modification history enter the cure models.
- **ADR-002 consistency:** natural. 90+ is the absorbing default state (ADR-002 B1); flagged payment
  pauses freeze the delinquency state (stop-the-clock: the month counts as "stays", not a roll);
  a modification while 60+ days late is a transition to default; `XX` carried forward per ADR-002 E.
- **Number of models:** logistic family: **3** (one multinomial model per starting state: current,
  30, 60). Challengers (LightGBM, EBM) **only on the key transitions**, especially current -> 30
  (where most risk starts and most rows are): 2 more. Total **5 models** (plus prepayment comes inside
  each multinomial model).
- **Data:** a rebuilt feature table with the state last month and this month (from the Phase 1 panel's
  ADR-002 delinquency counts); current -> current months sampled and weighted as now; every
  delinquent month kept (they are rare).
- **Databricks minutes (estimates from 17 Nov measurements):** feature rebuild about 15; logistic
  models about 8-12 each (current -> state is the large one), about 30 for the three; LightGBM
  challenger on current about 12; EBM challenger on current about 50-60 on a 1-in-4 sample (or skip
  EBM for a transition and keep it for current only). **About 110-120 minutes in all, spread over 3
  days**, plus the engine in Phase 4.
- **Against:** the most work (new feature table, three models, matrix projection in the engine);
  errors compound over months (validated by projecting the 2017-2019 and test periods).

### (b) Reserve-date PD (12-month and lifetime PD from the starting state)

Cohort approach used in many bank PD scorecards: take snapshots (for example each quarter-end), and
for each loan alive at the snapshot predict the chance of default within 12 months, 24, 36 and so on
(a term structure), using only what is known at the snapshot.

- **Phase 4:** gives lifetime PD directly from the reserve date; no month-by-month delinquency
  projection; but the ADR-003 forecast and reversion of macro inputs must be squeezed into a few
  summary inputs per horizon (for example the average unemployment change over the next 12 months).
- **Stress:** scenarios enter only through those summaries, so the effect is coarser and harder to
  trace than in (a).
- **Macro links / cures:** cures are inside the outcome; their post-2016 change shows as a level
  shift again (the same problem as now, measured over 12 months instead of one).
- **ADR-002 consistency:** the outcome is "defaulted within the horizon" by ADR-002's definition;
  consistent, but the stop-the-clock months only affect the label, not a modelled state.
- **Number of models:** 12-month PD plus 2-3 longer horizons (or one discrete-time survival model by
  horizon year) per family: **about 3 per family, 9 for three families** (fewer if the challengers
  only get the 12-month model).
- **Databricks minutes:** snapshot table about 15-20; each model is fast (one row per loan per
  snapshot, no sampling needed): **about 50-70 in all, 1-2 days.**
- **Against:** loses the monthly dynamics that CECL forecasting and reversion are built on; the
  starting state (current vs delinquent) still needs separate treatment.

### (c) Segments only (current vs delinquent)

Keep today's next-month model, but fit and calibrate it separately for loans current last month and
loans already behind.

- **Phase 4:** fixes the calibration split, but **not** the projection problem: a current loan's
  future months still need to know when it falls behind; the engine would have to assume loans stay
  current until default (understating lifetime defaults) or borrow a roll-rate assumption from outside
  the model.
- **Stress:** limited to the current-loan model's macro sensitivity.
- **Macro links / cures:** the delinquent segment's model absorbs the cure-rate change, but cure and
  roll are not separated.
- **ADR-002 consistency:** unchanged from today.
- **Number of models:** 2 segments x 3 families x 2 causes (the tree families use one model per cause):
  **up to 10 models**, fewer with challengers only on one segment.
- **Databricks minutes:** like 17 Nov per segment: **about 60-90 in all.**
- **Against:** a step towards (a) that leaves the lifetime question open.

### What stays optional (ADR-010, as amended)

The origination-era feature and the 2014-2016 level calibration are optional extras under any design,
tested only if the evidence supports them (for example era as an input to the current -> 30 model).

## Comparison

| | (a) multi-state | (b) reserve-date PD | (c) segments only |
|---|---|---|---|
| Lifetime PD from the reserve date | yes, month by month | yes, by horizon | no (needs an outside assumption) |
| ADR-003 monthly forecast and reversion | natural | through summaries | partial |
| Stress tracing | by transition | coarse | limited |
| Post-2016 cure rates | modelled where they happen | a level shift | absorbed, not separated |
| ADR-002 | natural (absorbing 90+, frozen state in pauses) | label only | as today |
| Models (logistic everywhere, challengers on key parts) | 3 + 2 | about 3-5 | up to 10 |
| Databricks minutes | about 110-120 over 3 days | about 50-70 | about 60-90 |

## Consequences

- Whatever is chosen: the Phase 2 plan, ADR-009's application (which models are compared and on what
  validation measures, for example AUC of current -> 30 and projected 12-month default rates by
  vintage) and the model card are updated; the out-of-time test stays untouched until Martins approves.
- (a) or (b) need a rebuilt feature table; (a) also changes the Phase 4 engine to a matrix projection.

## Decision

*Pending: Martins writes this section in his own words (design, families on which transitions, and why).*
