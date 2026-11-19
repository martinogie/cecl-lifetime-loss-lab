# Default model v2: plan for Martins's approval (no training yet)

- **Status:** proposed, 19 November 2026. Nothing below is built or trained.
- **Why:** model v1 (the locked logistic set, `@challenger`) failed the out-of-time test (L-032:
  projected defaults 0.54 of actual, July 2022 - June 2026). The diagnosis
  (`out_of_time_diagnosis.md`) put about three quarters of the gap on rate-locked current loans
  falling behind more often than the model expects, and the rest on late loans' path memory (L-030)
  and rising household stress the inputs did not show. Martins's change of plan (19 Nov 2026): fix the
  default model now, as v2, before Phase 4.
- **v1 stays on record:** failed (L-032), `@challenger` in dev, not deleted, not overwritten. v2 is
  registered under its own names (section 8), so v1's registration and alias do not move.

Educational model on Freddie Mac conforming mortgages; not an audited reserve and not accounting
advice.

## 1. Design: unchanged structure, all 9 models

The same multi-state design as v1 (ADR-011): for each starting state (current, 30 days, 60 days late)
one model predicts next month's state (current, 30, 60, default, prepaid); three families (logistic,
LightGBM with monotone constraints, EBM) at each step, so **3 x 3 = 9 models**. Same default definition
(ADR-002), same sample weighting (ADR-007), same projection idea.

**One change to the projection is needed, and I recommend it (decision D1).** The new memory inputs
(section 2b) depend on each loan's path: "worst state in the last 6 months" and "months since the first
missed payment". v1's projection carries probabilities split into slices (L-026), which already
approximate one history input; adding two more would multiply the slices and the approximation error.
**Proposal: a path simulation** (Monte Carlo): each loan is played forward along a fixed number of
random paths (fixed seed, so it re-runs to the same number), and every history input is computed
exactly on each path. This is also the fix named for L-030 and L-026. The number of paths is set so
that simulation noise on total projected defaults is under 1% (checked on the 2017-2019 projection,
where v1's slice projection is the reference). v1's slice projection stays in the code, unchanged, so
v1's results can be re-run exactly.

## 2. Inputs

All 21 v1 inputs are kept (including state last month, home-price gain since origination, SATO and
the rate incentive). New:

**a. Rate lock** (from the diagnosis, finding 1):

| Input | Meaning | Constraint |
|---|---|---|
| `rate_incentive` (kept) | note rate minus the market 30-year rate last month, points | unchanged: payoffs up with it; free for rolls |
| `locked_1` | 1 if the note rate is at least 1 point **below** market | **none** (the data decides) |
| `locked_2` | 1 if at least 2 points below market | none |
| `locked_x_age` | `locked_1` x years since the first payment (seasoned locked loans were worst: 0.62) | none |

Why this fixes v1's mistake: v1 learned one smooth effect, "further below market = safer", from
1999-2016, when a deep discount was rare and meant a strong, recently refinanced borrower. The bands
let the model give locked-in loans their own roll rate instead of extending that line. No monotone
constraint on any lock input for rolls to worse states: v1's lesson is that the direction is not
known in advance. Logistic gets the bands and the interaction as explicit inputs (it cannot find
interactions itself); LightGBM finds interactions itself; the EBM is allowed the pairs
lock x loan age and lock x late history. The bands use the 1- and 2-point cut points named in the
diagnosis; they are fixed now, not searched.

**b. Longer memory** (L-030):

| Input | Meaning |
|---|---|
| `worst_state_6` | worst state in the 6 months before t: 0 current, 1 = 30, 2 = 60 days late |
| `months_since_first_miss` | months since the loan first became 30+ days late (capped at 60) |
| `ever_missed` | 1 if the loan has ever been 30+ days late (so "never" is not read as 0 months) |
| `paused_12` (proposed, decision D2) | 1 if any of the last 12 months was a flagged payment pause (flags from 2014, L-016). The diagnosis found post-pause loans rolled to 30 days at 4.1% a month vs 0.4%, predicted at 0.46; v1 sees only last month's pause |

Directions: more history of trouble -> more rolls (monotone, the same business reason as v1's late
history); `months_since_first_miss` free (a long-ago miss followed by clean years is different from a
recent one).

**c. Household stress** (rising defaults in 2023-2026 while unemployment stayed near 4%):

| Candidate (national, monthly) | FRED series | In the Fed's 2026 scenarios? |
|---|---|---|
| CPI inflation, 12-month change | CPIAUCSL | yes (CPI inflation rate) |
| Real disposable income growth, 12-month | DSPIC96 | yes (real disposable income growth) |
| Credit-card delinquency rate (banks, 30+ days) | DRCCLACBS (quarterly) | **no** |

**Proposal: only candidates the Fed scenarios provide are eligible for v2** (CPI inflation, real
disposable income growth). Phase 4 must feed every input a baseline, adverse and severely adverse
path; card delinquency has no Fed path and would need a separate model linking it to unemployment
(a satellite model, more model risk). It is screened and reported for information only (decision D3).

**Keep rule (fixed now, decided on the historical folds only):** a candidate is added, one at a time,
to the current-loan logistic model; it is kept only if it improves the **average** walk-forward log
loss of the current step over folds F1-F4 (section 3) by at least **0.2%**, worsens no single fold
by more than 0.1%, and its sign makes business sense (more inflation or falling real income -> more
rolls). Kept candidates go into all 9 models. Why so small a threshold: a national variable moves the
whole book together and its gain in log loss is small even when it matters; the projection bars
(section 4) are the real test.

New data to download (the existing FRED pipeline, Martins's key already in `.env`; no new secret):
CPIAUCSL, DSPIC96, DRCCLACBS, plus the CPI and disposable-income columns of the Fed's 2026 scenario
files (already downloaded; silver currently keeps six variables).

## 3. Data split

| Period | Months | Use |
|---|---|---|
| **Training** | Jan 1999 - Dec 2019 **and** Jul 2022 - Jun 2024 | fitting and tuning (about 58 million loan-months, 1.45x v1) |
| COVID | Jan 2020 - Jun 2022 | **not fitted** (proposal below); history inputs still computed from these months |
| **Holdout exam** | Jul 2024 - Jun 2026 (24 months) | run once, after selection. Labelled **"not fully clean: the rate-lock idea came from the 2022-2026 diagnosis"** |
| Clean test | future Freddie quarters (Phase 8 monitoring) | the first data nobody has looked at |

**COVID proposal (decision D4): keep January 2020 - June 2022 out of fitting, as in v1.** The
sensitivity run (validation report) showed that adding those months lowers projected defaults by about
12%, because forbearance stopped the delinquency clock for millions of loan-months; v1's problem is
under-prediction, so adding them pushes the wrong way. They stay in the data for the history inputs:
a loan's pauses and misses during COVID still count in `paused_12`, `worst_state_6` and the late
history after June 2022. The sensitivity run (training with them) is repeated for v2, information only.

**Walk-forward folds** (each: train on everything before the scored period, same exclusions):

| Fold | Train to | Scored | Role |
|---|---|---|---|
| F1 | Dec 2007 | 2008-2010 (crisis) | tuning, selection, projection check |
| F2 | Dec 2010 | 2011-2013 | tuning, selection, projection check |
| F3 | Dec 2013 | 2014-2016 | tuning, selection, projection check |
| F4 (proposed) | Dec 2016 | 2017-2019 (v1's validation years) | tuning, selection, projection check |
| F5 | Dec 2019 | Jul 2022 - Jun 2024 | **reported only**, labelled not clean |

**An honest limit of the folds (please read).** Deeply locked-in loans barely exist before 2022 (0.1%
of current loan-months in 1999-2016). So folds F1-F4 can show that the new inputs **do no harm** on
history, but they **cannot show the rate-lock fix works**: its effect is learned almost entirely from
July 2022 - June 2024, which is training data. F5 (trained before any lock-in) will probably still fail;
it is reported to show that. The only checks of the rate-lock fix are the holdout (not fully clean) and
future quarters (clean). The documents will say this wherever v2's results appear.

## 4. ADR-009 Amendment 3 (pass rule and selection rule, written before training)

Drafted in full in `docs/adr/009-model-selection-rule.md` (status: proposed; the Decision is left for
Martins). In short:

| Part | Rule |
|---|---|
| Tuning | v1's grids, unchanged; best **average log loss over F1-F4** per step (all destinations); each fold reported |
| Calibration bars | v1's bars (0.80-1.25, cells of 200+ moves: by year, vintage, whole period, late-history band) on each fold's scored months, **plus a rate-lock band bar** (incentive bands 2+ below, 1-2 below, 0-1 below, 0-1 above, more than 1 above) judged in-sample on Jul 2022 - Jun 2024, labelled in-sample |
| Selection | the 2% rule per step on the average fold log loss; a challenger must pass every bar and give reason codes; ties to the simpler family; the combination check as in Amendment 1 |
| **Walk-forward requirement** | the chosen combination, refitted on each fold's training months (the tuning fits are reused), projected 36 months from the start of F2, F3 and F4: **total projected defaults within 0.80-1.25** and **every judged default cell by year** within 0.80-1.25. F1 (a crisis the fold has never seen) is judged on direction: its total must be **closer to 1.0** than the v1-input logistic benchmark on the same fold. If the chosen combination fails, the combination check swaps steps back to logistic; if all-logistic also fails, it is recorded and I stop for Martins (no change to inputs or rules on my own) |
| **Holdout pass** | full projection from 1 July 2024 to June 2026: **every judged default cell** (by year and by vintage, 200+ actual defaults) within 0.80-1.25, **and** total defaults within 0.80-1.25. Payoffs reported; payoff cells outside the bar recorded under L-028, not failing. Each transition's calibration, log loss, AUC, Gini reported |
| Run once | the holdout runs once; no model is chosen, swapped or tuned on it; any relaunch needs Martins's approval (his rule, 19 Nov 2026) |
| Promotion | a holdout pass makes v2 usable in Phase 4 as `@challenger`, labelled "holdout passed, not fully clean". `@champion` only after Martins's written review **and** the first clean Freddie quarter (Phase 8) with total defaults within 0.80-1.25 (decision D5). A fail: v2 stays `@challenger`, recorded; Phase 4 falls back to the L-032 overlay route |

## 5. Expectations file

`docs/phase2/model_v2_holdout_expectations.md`: the defaults and payoffs sections are blank for
Martins. The holdout does not run until he marks it final (the v1 guard, reused).

## 6. LGD and EAD (Phase 3): unchanged

No refit. After selection, the combined expected loss (v2 default probabilities x Phase 3 LGD x EAD)
is checked against actual losses on F4 (2017-2019 starts, losses almost all resolved), before the
holdout. On the holdout it is reported as indicative only: most 2024-2026 defaults have no reported
loss yet (L-004).

## 7. Cost and timing

Databricks minutes (estimates from v1's measured runs: feature build 14.2, nine trainings about 216
with three folds, the current-loan EBM alone 80.7, a projection about 8):

| Step | Estimate (minutes) |
|---|---|
| Feature build with the new inputs | 20 |
| Household-stress screening (logistic, current step, 4 folds) | 25 |
| 9 models, tuned over 4 folds, on 1.45x the data | 400-450 (EBM about 160 of it) |
| Walk-forward projections (4 folds x chosen and benchmark, path simulation) | 80 |
| COVID sensitivity (information) | 30 |
| **Total before the holdout** | **about 550-600 (9-10 hours)** |
| Holdout exam, later, once | about 60 |

**Option to cut about 100 minutes (decision D6):** fit the current-loan EBM on a fixed 1-in-2 sample of
current loan-months, weighted by 2 (the ADR-007 method). It has no effect on the selection rule; the
learning curve is reported.

**Timing.** Today is Thursday 19 November. Code, tests and local checks: Thursday and Friday. Databricks:
Friday evening to Saturday, in batches, each job started outside the blocked windows (never 02:30-04:00
Chicago; never Monday before 13:00; the scheduling guard checks every launch). Results and the stop
before the holdout: Saturday 21 November if nothing fails. The VM and Bank Health are not touched (all
on Databricks and the PC).

## 8. What gets built after approval

- `cecl/features.py`, `ingestion/feature_build.py`: the new inputs, contract ranges, tests on made-up
  data.
- `ingestion/fred.py` contract: three new series; Fed scenario silver keeps CPI and disposable income.
- `cecl/models/simulate.py`: the path simulation (v1's `multistate.py` unchanged).
- Training pipeline: v2 split, F1-F5, the stress screening, the walk-forward projection requirement.
- MLflow: v2 runs tagged `model_version=v2`; registered as `cecl_lab_dev.ml.pd_v2_transition_{current,30,60}`
  with `@challenger` (v1's `pd_transition_*` and its alias stay as they are).
- Documents: ADR-009 Amendment 3 (after Martins's decision), selection table, change log, model
  inventory (M-01 v2 as a new entry), limitations log updates.
- Stop before the holdout exam.

## Decisions for Martins

| # | Decision | My recommendation |
|---|---|---|
| D1 | Projection: path simulation instead of slices for v2 | yes (exact history inputs; fixes L-026 / L-030's cause) |
| D2 | Add `paused_12` (payment pause in the last 12 months) | yes |
| D3 | Only household-stress inputs with a Fed scenario path are eligible (card delinquency information only) | yes |
| D4 | COVID months stay out of fitting, used for history inputs; sensitivity repeated | yes |
| D5 | `@champion` needs the holdout pass, your review and one clean quarter | yes |
| D6 | Current-loan EBM on a 1-in-2 sample to save about 100 minutes | your call; I lean yes |
| - | Add fold F4 (2017-2019) to the three you named; F5 reported only | yes |
| - | Amendment 3 as drafted (especially the F1 "closer than benchmark" rule and the holdout total bar) | please read and edit |
