# Changes to roadmap v2 (decided after it was written)

The roadmap PDF is private (prepared for Martins, kept on his PC, never committed). Decisions that
change what it describes are listed here, each with its ADR.

| Date | Roadmap v2 says | Changed to | Where |
|---|---|---|---|
| 15 Nov 2026 | COVID full-data vintage 2020 or 2021 | 2019 (optionally 2017) | ADR-001 change note |
| 15 Nov 2026 | Fed paths from July 2026 as changes from the Fed's 2026 Q2 value | Fed path shifted to the reserve date (its first quarter becomes 2026 Q3) | ADR-003 amendment |
| 16 Nov 2026 | Phase 2 training on the PC | Features and training on Databricks, the PC as the reference | ADR-001 change note; Phase 2 plan |
| 17 Nov 2026 | Section 5.1: a competing-risks hazard model (default and prepayment each month) | **Extended to a monthly multi-state (delinquency-transition) model:** current, 30, 60, 90+ default (absorbing), prepaid, with cures; logistic, LightGBM and EBM fitted for every starting state (9 models), the ADR-009 rule choosing at each step | ADR-011; ADR-009 Amendment 1 (accepted); docs/phase2/plan_multistate.md |
| 18 Nov 2026 | Phase 7: a Methods page (PD, LGD, EAD, CECL explained simply); Phase 8: monitoring of PD drift and calibration | **Added:** a website "Models & Methods" page (data preparation to testing) and model results published automatically after every training; Phase 8 drift monitoring split into a weekly score-stability check and quarterly performance monitoring with alerts; training curves change only on retraining. Not built yet | This file, section "Phase 7 and 8 additions"; docs/phase2/plan_multistate.md, "Model documentation" |
| 19 Nov 2026 | Section 5.1: three model families (logistic, LightGBM, EBM) | **Model v2** (after v1 failed its out-of-time test): logistic and LightGBM only, 6 models; the EBM is dropped for v2 (v1's records keep it) | ADR-009 Amendment 3; `docs/phase2/model_v2_plan.md` |

## Phase 7 and 8 additions (Martins, 18 November 2026; not built yet)

### Phase 7: website "Models & Methods" page

A detailed, plain-English method from data preparation to testing, **aggregates only** (Freddie Mac's
terms: never loan rows). Sections:

1. Data sources and data contracts (what each contract checks; a contract failure stops the load).
2. Cleaning: the definition of default (ADR-002), status codes, censoring, payment pauses.
3. Missing values: the rules as built (see the Phase 2 plan, "Model documentation").
4. Outlier treatment: exactly what the project does today (contract rejections, sanity ranges that
   stop a build, scorecard binning), and the decision on capping: **none for the transition models**
   (binning and trees already ignore extreme values; capping would hide real HARP loans); capping for
   LGD decided separately in Phase 3 (Martins, 18 November 2026).
5. Features (the 18 transition inputs, each with its meaning and expected direction).
6. Sampling and weights (current -> current months 1 in 25, weighted; every measure weighted).
7. The multi-state design (ADR-011): states, transitions, the projection.
8. Tuning: the walk-forward folds (ADR-009 Amendment 1).
9. Training: the families per step (v1: logistic, LightGBM and EBM; v2: logistic and LightGBM),
   training curves, the fit table.
10. Selection: the ADR-009 rule with its amendments, and the winner table.
11. The out-of-time test: protocol, pass mark (Amendment 2) and result.

**Published automatically after every training** (the training job exports these artifacts; the site
refreshes from them, only when CECL_ENV=prod, per ADR-004):

- the champion's training curves (learning curve and fit table; LightGBM per-round curves if it wins
  a step);
- per transition (current, 30, 60), for every challenger and the champion: log loss, AUC, Gini,
  calibration (predicted / actual, by year, vintage and late-history band) and PSI;
- the winner table (one row per step x family, with the reason);
- the champion's ROC curves, one per transition, with **current -> 30 highlighted**.

**Added Phase 7 acceptance criteria:**

- The Models & Methods page covers sections 1-11 above in plain English, with aggregates only, and
  passes the same checks as every page (no loan rows, caveats, CSP, no tracking).
- The outlier section states what is done today and the decision (no capping or winsorising for the
  transition models; LGD decided in Phase 3).
- The training job exports the curves, the per-transition metrics, the winner table and the ROC
  curves (points, not pictures) as artifacts; the site build reads them; a test checks the export
  contains no loan-level data.
- After a training run, the site shows the new results without manual editing (shown on the preview
  first; publishing still needs Martins's approval).

### Phase 8: drift and performance monitoring

- **Weekly (Wednesday):** score the champion on the live book with the latest economic data; publish
  score stability (PSI against the training baseline, per transition), data freshness (each source's
  latest month vs expected) and a status (green / amber / red). If no new economic month has arrived
  since the last run, the scoring is skipped and only freshness is reported (no Databricks minutes).
- **Quarterly (after each Freddie release):** full performance monitoring on the new quarters:
  predicted / actual per transition (by year, vintage and late-history band, as in ADR-009), AUC and
  ROC on recent data, and a trend chart over time (one point per quarter); alerts (Slack and Discord,
  `[CECL]`) when a judged cell is outside the ADR-009 bars.
- **Training curves change only when the model is retrained:** yearly, or when quarterly monitoring
  triggers a review (a judged default cell outside 0.80-1.25, or PSI above 0.25 for a transition, two
  quarters in a row; approved by Martins, 18 November 2026).
- All jobs follow rule 6 (never Monday before 13:00 Chicago, never 02:30-04:00) and record their
  minutes in the run log.

**Added Phase 8 acceptance criteria:**

- The weekly job has run on at least two Wednesdays with its PSI, freshness and status published, and
  has skipped scoring correctly when no new data arrived.
- The quarterly job has run on at least one Freddie release, with the per-transition calibration,
  AUC/ROC and the trend chart published, and an alert shown to fire on a test breach.
- The retraining trigger is written down (yearly, or the review conditions above) and tested.

**Expected Databricks minutes** (from this week's measured runs: the full Freddie load 54.6, the
transition feature build 13.9, the 2017-2019 projection 3.8-4.3, a logistic evaluation under 1):

| Job | Minutes per run | Per month |
|---|---|---|
| Weekly score-stability check | about 2-4 when a new economic month is scored; 0 when skipped (freshness runs in Airflow) | about 3-8 (one new economic month a month) |
| Quarterly performance monitoring (on top of the quarterly data refresh) | about 10-20 (score the new quarters per transition, calibration, AUC/ROC, trend) | about 3-7 |
| Quarterly data refresh it depends on (already part of the quarterly close) | about 70-90 (Freddie reload about 55, transition features about 14, projection about 5) | about 25-30 |
