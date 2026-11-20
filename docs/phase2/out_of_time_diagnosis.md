# Why the out-of-time test failed: diagnosis for the next model version

Written 19 November 2026, after Martins's review (`out_of_time_review.md`), decision (1): diagnose the
failure **for the next model version only**. Scoring only, about 2 hours; no model was retrained,
re-chosen or adjusted on the test years, and the locked models stay `@challenger`. Any fix suggested
here must be validated on a new period before use (ADR-009 E). Aggregates only.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.

## Summary in plain English

| Finding | How much of the gap (projected defaults 0.54 of actual) it explains |
|---|---|
| **1. Rate-locked current loans fall behind more often than the model expects.** The current-loan model learned in 1999-2016 that a loan whose rate is far *below* the market rate rarely gets into trouble. In 2022-2026 most loans were exactly that (rates of 3% against a market of 6-7%), and they fell behind about 1.5 times as often as predicted. | **About three quarters.** Correcting only the roll from current to 30 days late, by rate-lock band, lifts projected defaults from 0.56 to 0.88 of actual (sample of a third of the loans). |
| 2. Payoff errors (L-028, L-031) | **About none.** Setting payoffs to their actual level by vintage changes projected defaults by about 1%. |
| 3. Loans already 30 or 60 days late in July 2022 (L-030) | **Part of the remaining quarter.** Even with finding 1 corrected, loans 30 days late at the start are projected at 0.69 of their defaults. |
| 4. Rising stress the inputs do not show | **Part of the remaining quarter.** Actual defaults per loan-month rose every year (2.8 per 10,000 in 2022 to 6.6 in the first half of 2026) while unemployment stayed near 4% and house prices high, so the model's economic inputs pointed to low risk. |

**The data check passed:** the test years' defaults are counted the same way as in training and match
Freddie Mac's own published figures (details below), so the failure is the model, not the data.

## a. Data and definition check

- **Same definition, same counting.** The panel applies ADR-002 the same way in every year. Our sample
  reproduces Freddie Mac's published "ever 90+ days late" rates by vintage (Release 48 summary
  statistics) closely: for the 2015-2022 vintages within a few tenths of a point (for example 2017:
  6.16% vs Freddie 5.89%; 2022: 3.57% vs 3.56%). The rise in actual defaults is real.
- **Forbearance and disaster flags are rare in the test years:** 0.61% of loan-months flagged in 2022,
  0.32% in 2023, 0.13-0.20% in 2024-2026 (2.8% in 2020). Stopped-clock months (reported lateness differs
  from the lateness ADR-002 counts) are equally rare (0.13-0.63%).
- **No regional (hurricane) pattern:** the roll to 30 days is under-predicted in every large state
  (0.65-0.81; Florida 0.81, Texas 0.79, California 0.71).
- **Post-forbearance re-defaults are a new population.** 20-25% of 2023-2025 defaults came within 12
  months of a payment pause (1-4% in 2014-2016). These are real defaults, counted the same way, but the
  model never saw many of them: flags exist only from 2014 (L-016) and the model's pause input covers
  only last month. Loans paused in the past 12 months rolled to 30 days at 4.1% a month against 0.4% for
  the rest, and were predicted at 0.46 of that. They are 13% of the one-month roll gap (overlapping with
  finding 1: most of them are rate-locked too).

## b. The roll from current to 30 days late, test years (predicted / actual, one month)

| Rate incentive (note rate minus market rate) | Share of current months | Training 1999-2016 | Validation 2017-2019 | **Test 2022-2026** |
|---|---|---|---|---|
| 2 points or more below market (locked in) | 57% | 0.74 (rare: 0.1% of months) | 1.47 (rare) | **0.67** |
| 1-2 below | 15% | 0.83 | 0.91 | **0.57** |
| 0-1 below | 14% | 0.96 | 1.08 | 0.83 |
| 0-1 above | 12% | 1.00 | 1.11 | 1.08 |
| more than 1 above | 2% | 1.03 | 1.14 | 1.19 |

Locked-in loans (incentive 1 point or more below market) account for **96% of the one-month gap** (15,300
of 15,900 missed rolls). By year the ratio is 0.67 (2022), 0.69, 0.74, 0.80, 0.83 (2026): it improves as
fewer loans are deeply locked in. By loan age, seasoned loans are worst (7+ years 0.62; 0-1 years 1.00).
By vintage, 2009-2019 loans 0.53-0.67, 2020-2022 loans 0.71-0.78, 2023-2025 loans 0.99-1.40.

Why: in the training years a rate far below the market was rare and meant a strong borrower who had
recently refinanced; a rate above the market meant a borrower who *could not* refinance (often in
trouble in 2008-2012). The model learned "below market = safe". In 2022-2026 almost everyone was below
market because rates jumped, not because they were strong borrowers, so that signal no longer meant
safety. The model's other inputs moved the same way: the test book had low unemployment (4.0% vs 6.5% in
training), high equity (house prices up 44% since origination on average vs 7%) and good credit scores,
all of which the model reads as low risk.

## c. Why the projection falls further behind each year

Projected / actual defaults by year: 1.26 (July-December 2022), 0.61, 0.48, 0.47, 0.32 (January-June
2026) on the sample (the full test: 1.07, 0.57, 0.46, 0.46, 0.36).

- In the first months, defaults come from loans **already late in July 2022**, which the 60-day model
  handles well, so the projection starts on target.
- Later, defaults come from loans that were current in July 2022 and fell behind afterwards. Each month
  the model lets too few of them fall behind (finding 1), and the shortfall **accumulates** along the
  path current -> 30 -> 60 -> default, which takes three months or more.
- On top of that the actual default rate kept rising (finding 4), which the inputs did not signal.

With the roll corrected by rate-lock band, the yearly ratios become 1.46, 0.90, 0.81, 0.87, 0.63: most of
the decay disappears up to 2025; 2026 stays low (the rising stress, and loans late at the start).

## What this means for the next model version (not applied now)

1. A **rate-lock-aware** current-loan model: the rate incentive should not be allowed to keep lowering
   risk once a loan is deeply below market (for example a capped or two-sided effect, or an input that
   separates "locked in" from "recently refinanced"), and training should include periods with many
   locked-in loans. It must be validated on a period the locked models were not tested on.
2. An input for **a payment pause in the past 12 months** (not only last month).
3. The **path memory** for late loans (L-030; a path simulation in the projection).
4. Phase 4: the **L-032 overlay** under ADR-006, sized from this evidence and capped (Martins's decision
   (3)): the rate-lock correction alone would raise projected defaults by about 58% (0.56 to 0.88).

## Method notes

- One-month tables: the locked current-loan logistic model scored on every current-start transition row
  of the test months (735,038 rows, weighted), with "paused in the past 12 months" joined from the panel.
- Projection counterfactuals: the locked combination projected from 1 July 2022 for 48 months on a fixed
  1-in-3 sample of loans (85,823 loans, 1,346 actual defaults; baseline 0.56, close to the full test's
  0.54). "Roll fixed" multiplies the current-loan model's roll-to-30 chance by 1 / (one-month test ratio)
  in each rate-incentive band; "payoff fixed" multiplies the payoff chance by actual / projected payoffs
  per vintage. Both are diagnostic only and use the test years, so neither may be used as a fix.
