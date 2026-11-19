# Phase 3: LGD and EAD model documentation (draft for Martins's review)

Draft, 19 November 2026. Code: `cecl/lgd.py`, `cecl/ead.py`, run by `scripts/fit_lgd_ead.py` on the
local Freddie Mac sample (50,000 loans a year, 1999-2026; ADR-001). Numbers: `docs/phase3/results.json`
(aggregates only). Educational model on Freddie Mac conforming mortgages; not an audited reserve and
not accounting advice.

Phase 3 acceptance criteria (roadmap section 7):

| Criterion | Status |
|---|---|
| LGD two-step model from actual losses; downturn behaviour shown | **Met**: below, "LGD" |
| EAD projection matches actual balances for a held-out set within tolerance | **Met**: within 2% at 12, 24 and 36 months |
| Model documentation drafted for both | **This document** (for Martins's review) |

## LGD (M-02): how much is lost when a loan defaults

**Data: every default, with the same definition as PD.** One row per first default in the panel
(ADR-002: 90+ days late or a loss event), 52,713 defaults, each followed to how the loan ended:

| How the default ended | Defaults | Treatment |
|---|---|---|
| Loss event (codes 02, 03, 09, 15) with Freddie's loss fields | 19,675 | severity = actual loss / balance at default |
| Paid off in full later | 16,413 | no loss (0) |
| Current again at the end of the data | 4,718 | no loss (0) |
| Sold as a re-performing loan or bought back as a defect (16, 96) | 7,166 | left out: Freddie reports no loss (mostly recovered loans, so leaving them out leans conservative) |
| Still delinquent at the end of the data | 3,209 | left out (unresolved) |
| Loss event, loss not yet reported | 1,532 | left out (unresolved, L-004) |

A first version used loss events only; that measures the loss when a house is sold, not the loss per
default, and would have overstated LGD about twofold (0.46 vs 0.27 per default on the training years),
because almost half of the defaults that resolve end with no loss. A gain on sale counts as no loss;
179 severities above 150% are capped at 150% for fitting (**capping proposed; Martins decides**, as
planned in roadmap_v2_changes.md).

**Model: two steps** (bank-standard for mortgages, where many defaults lose nothing):
1. the chance of any loss; 2. the size of the loss when there is one. LGD = 1 x 2.
Both are gradient-boosted trees (depth 3, 300 rounds) with **monotone constraints**: LTV at default up
-> loss up; mortgage insurance up -> loss down; house prices rising over the last year -> loss down;
judicial state -> loss up; larger loan -> loss down (fixed costs). Inputs are all known at default, so
the Phase 4 engine can supply them month by month from the projected balance and the house-price path:

| Input | Why |
|---|---|
| LTV at default (balance / original value x state house-price change since origination) | The house is the collateral: the less equity, the bigger the loss. This ties LGD to house prices |
| State house-price change over the 12 months before default | Falling markets sell lower and slower, and fewer borrowers can sell their way out |
| Mortgage insurance cover (%) | MI pays part of the loss |
| Judicial foreclosure state | Court process: longer timelines, more interest, taxes and upkeep |
| Original balance (log) | Fixed costs weigh more on small loans |
| Credit score, investor property | Small effects, left free |

The time to resolution, the price change after default and the way the default ended are **not**
inputs (not known when the reserve is set).

**Split:** fitted on defaults up to 2016 (32,319 resolved, with inputs), checked on defaults from 2017
(8,252), the same time split as PD (ADR-009 A). Recent defaults are a biased check: cures resolve
quickly while losses take years, and hundreds of 2023-2026 defaults are still delinquent (left out), so
the fair check is defaults from **2017-2021**, almost all resolved.

**Results (balance-weighted LGD per default, as a reserve uses it):**

| | Defaults | Actual | Two-step model | Benchmark: average by LTV band |
|---|---|---|---|---|
| Fit, defaults to 2016 | 32,319 | 0.266 | 0.266 | — |
| **Check, defaults 2017-2021 (resolved)** | 4,238 | **0.044** | **0.072 (1.6x, conservative)** | — |
| Check, all defaults from 2017 | 8,252 | 0.025 | 0.055 | 0.179 |
| Any loss at all, 2017-2021 | | 21% | 29% | |
| Size of the loss when there is one, 2017-2021 | | 0.26 | 0.30 | |

By default year in the fit years the model tracks the crisis and recovery (2008: 0.40 actual vs 0.32;
2009 0.34 vs 0.33; 2012 0.24 vs 0.27; 2016 0.12 vs 0.12); the earliest years are over-predicted (few
events). By LTV band, judicial state and MI the fit-year groups match within 0.01. **On the check years
the model over-predicts**: since 2016 more defaults cure (rising house prices, post-crisis loss
mitigation), which the 1999-2016 data does not show; the error is conservative (it overstates loss) and
is recorded as L-033. It is not offset against the PD under-prediction (L-032): each is reported on its
own.

**Downturn behaviour** (the same defaults with house prices lower; LTV and the 12-month price change
moved accordingly):

| House prices | 0 | -10% | -20% | -30% |
|---|---|---|---|---|
| LGD, defaults from 2017 | 0.06 | 0.17 | 0.27 | 0.44 |
| LGD, all defaults | 0.22 | 0.34 | 0.48 | 0.60 |

LGD rises steeply when prices fall, as in the data: defaults in 2008-2011, when prices were falling,
lost 0.29-0.40 per default; those in 2020-2022, when prices were rising fast, about 0.02.

## EAD (M-03): how much is owed at default

**Method.** The balance follows the loan's payment schedule (rate, remaining term; the same function
as the multi-state projection), reduced by **partial prepayment** at a monthly rate by loan age,
estimated from 1999-2016 on a fixed 1-in-10 sample of loans (loan-months current this month and last,
unmodified, not paid off):

| Loan age (months) | 0-12 | 12-36 | 36-60 | 60-120 | 120+ |
|---|---|---|---|---|---|
| Extra principal a month (% of balance) | 0.125% | 0.077% | 0.093% | 0.127% | 0.260% |

Full prepayment and default are the transition models' job (the loan leaves the book).

**Held-out check:** loans current and unmodified in January 2017 (validation years), projected balance
vs actual balance later, for loans still current and unmodified then (balance-weighted):

| Months ahead | Loans | Schedule only | **With partial prepayment** | Median error per loan | Within 2%? |
|---|---|---|---|---|---|
| 12 | 23,898 | 1.009 | **0.997** | 1.1% | yes |
| 24 | 21,279 | 1.018 | **0.993** | 2.2% | yes |
| 36 | 18,624 | 1.028 | **0.989** | 3.3% | yes |

The schedule alone overstates balances by 1-3% (borrowers pay a little extra); with partial
prepayment the projection is within about 1% at 12-36 months, slightly low by three years.

**Where it is not mechanical:** modified loans and loans with deferred (non-interest-bearing)
balances do not follow the original schedule; the check excludes them, and the reserve starts from each
loan's reported balance at the reserve date, so a deferral already on the balance is carried. Interest
accrued while delinquent is not added to EAD (Freddie's loss already includes it in severity).

## Limitations (added to the limitations log as L-033 and L-034)

- L-033 (LGD): over-predicts loss per default since 2017 (1.6x on resolved 2017-2021 defaults:
  more cures than in 1999-2016); codes 16 and 96 left out (conservative); recent defaults unresolved
  (L-004); the way a default ends is not known in advance; severity cap at 150% pending Martins.
- L-034 (EAD): a single partial-prepayment rate per age band (no borrower or rate effects); slightly
  low by three years (0.989); modified and deferred balances follow the reported balance only.

## Where these enter Phase 4

The CECL engine multiplies, for each loan and future month: the chance of defaulting that month (the
multi-state projection, @challenger) x LGD (this model, fed the projected LTV and the scenario's
house-price path) x EAD (this balance projection). **The L-032 overlay** (Martins's decision (3)) is
applied after the modelled reserve, under ADR-006: sized by re-running the engine with the
under-prediction evidence from the out-of-time test, capped, logged and approved by Martins.
