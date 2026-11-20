# Written expectations before the model v2 holdout exam

- **Status:** final, confirmed by Martins (19 November 2026)

To be recorded **before the v2 holdout exam runs** (July 2024 - June 2026, scored once). Committed so
the expectation carries a timestamp earlier than the exam. It does not change the selection rule or
the pass rule (ADR-009 Amendment 3, E; locked by Amendment 5); it says in advance what we expect to see,
so the result can be read honestly.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.

### My expectation on defaults

I expect total projected defaults over July 2024 – June 2026 to be close to actual, between 0.90 and 1.10. v2 passed the three normal-period walk-forward exams at 0.92, 1.09 and 1.09, and unlike v1 it has learned from the rate-lock period (July 2022 – June 2024), which is the closest economy to the holdout.

By starting state: I expect loans current at the start to be close to actual (0.90–1.10). Loans 30 days late may still be somewhat under-predicted (around 0.80–0.95), because recovery and relapse paths remain the hardest to project. Loans 60 days late should be near actual (around 0.85–1.05), as the 60-day step has been the most reliable in every test.

The main risk is the newest vintages (2023–2024 loans). In the walk-forward exams the youngest vintages were often over-predicted (1.32–1.48), so I expect at least one recent vintage cell to come in above 1.25. Because the pass rule requires every judged default cell to be within 0.80–1.25, I think the overall pass is about a coin flip, even if the total passes.

On ranking: I expect AUC above 0.80 for the move from current to 30 days late, and lower (roughly 0.65–0.75) for the 30→60 and 60→default moves, where all loans are already struggling. I expect log loss to be better than v1's on the same period, because v2 includes the rate-lock and longer-memory inputs v1 lacked.

### My expectation on payoffs

I expect payoffs to remain the weakest part of the model (L-028). After the first year I expect total payoffs to be under-predicted (around 0.75–0.90), mainly for older, seasoned loans whose payoffs come from home sales and moves rather than refinancing. For the 2020–2021 low-rate vintages, v2 should do better than v1 (which over-predicted them at 1.48–1.79) because it has learned rate lock-in, but I still expect some cells outside the bar. Payoff failures are recorded under L-028 and do not fail the test under the rules.

### If it fails

The holdout rules are locked (Amendment 5). Whatever the result, it stands. If v2 fails, it stays @challenger with the failure recorded, and Phase 4 uses a documented overlay under ADR-006. If it passes, promotion to @champion still needs my written review and one clean future Freddie quarter (expected late January 2027).

## Facts available before the exam (no scoring)

- v1 on the same months (from the out-of-time test, already recorded): projected / actual defaults by
  year 0.46 (2024), 0.46 (2025), 0.36 (January-June 2026). These come from a projection started in
  July 2022, so they are not directly comparable with v2's projection started in July 2024.
- v2 selection: logistic at all three steps (`docs/phase2/model_v2_selection.json`). Walk-forward exams
  (total defaults): 2011-2013 0.92, 2014-2016 1.09, 2017-2019 1.09 (pass); 2008-2010 crisis 0.38 (failed;
  information only under Amendment 4, L-035); 2022-2024 information exam, trained to 2019, 0.72.
- Downturn-sensitivity check: tests 1-6 passed; test 7 failed as ratios (L-037) and passed in percentage
  points under Amendment 5. v2 is `@challenger` in dev (19 November 2026).
