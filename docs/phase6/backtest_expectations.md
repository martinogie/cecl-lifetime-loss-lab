# Written expectations before the Phase 6 backtests

- **Status:** blank, waiting for Martins's text.

To be written by Martins and committed **before** the crisis backtest (CB) and the rolling backtest
(RB) run, so the expectations carry an earlier timestamp than the results. They do not change the test
designs or the pass rules (`docs/phase6/test_designs.md`, committed first).

Educational model on Freddie Mac conforming mortgages; not an audited reserve.

### My expectation for the crisis backtest (2007-2010)

_(Martins)_

### My expectation for the rolling backtest (year-ends 2012-2022)

_(Martins)_

### If they fail

_(Martins)_

## Facts available before the tests (no scoring)

- v2's walk-forward crisis exam F1 (trained to December 2007, projected 2008-2010): projected / actual
  defaults 0.38 under the recorded count, 0.50 counted correctly (L-035, L-039).
- v2's normal-period exams F2-F4, counted correctly: 1.27, 1.37, 1.35 (v2 over-projects defaults in
  2011-2019; L-039 re-measurement).
- v2 holdout (July 2024 - June 2026), counted correctly: 0.92; in expected-loss terms 0.74 at the
  model's LGD (L-038).
- The booked LGD predicts 1.64 times the real loss per default on resolved 2017-2021 defaults (L-033).
- Few defaults before 2007 have losses, so the crisis-backtest LGD is fitted on a thin sample.
