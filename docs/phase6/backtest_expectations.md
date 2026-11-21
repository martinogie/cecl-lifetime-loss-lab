# Written expectations before the Phase 6 backtests

- **Status:** final, confirmed by Martins (20 November 2026)

To be written by Martins and committed **before** the crisis backtest (CB) and the rolling backtest
(RB) run, so the expectations carry an earlier timestamp than the results. They do not change the test
designs or the pass rules (`docs/phase6/test_designs.md`, committed first).

Educational model on Freddie Mac conforming mortgages; not an audited reserve.

### Crisis backtest (models fitted to December 2006, tested on 2007–2010)
I expect this test to fail. A model fitted only on data before 2007 has never seen a national house-price fall. The earlier v2 crisis exam projected only about 0.38–0.50 of actual defaults, so I expect total defaults around 0.35–0.55 of actual. I expect total losses to be even further below actual (around 0.25–0.50), because the LGD model fitted before 2007 learned from years when house prices were rising and losses were small. I think it is about a coin flip whether the model beats the simple average-default-rate benchmark, since that benchmark also comes from the calm pre-2007 years.

### Rolling backtest (11 year-ends, 2012–2022, refitted at each date)
I expect projected losses to be well ABOVE actual losses overall, so the test fails on the high side: roughly 1.5 to 2.5 times actual when summed over all dates. Three reasons: v2 over-projects defaults in normal periods (27–37% in the corrected walk-forward exams); the LGD model is conservative (1.64x); and after 2012 house prices recovered strongly, so real losses per default were small. I expect the early dates (2012–2014), whose LGD learned heavily from the crisis, to be the most over-projected, and fewer than 8 of the 11 dates to fall inside the wider band. In plain terms: I expect reserves would have been more than enough, not too little.

### Forbearance test
I expect the stop-the-clock rule to be supported: most of the extra "defaults" counted without it during 2020–2021 should have become current again within 12 months.

### If the tests fail
The results stand as recorded and are information for the validator; they do not change the booked reserve. Failures are logged in the limitations log, explained in the validation report, and carried into the v3 notes. A crisis-backtest failure confirms the known limit of data-driven models before an unseen type of downturn (L-035); a rolling-backtest failure on the high side shows the reserve method is conservative in normal and recovering markets.

## Facts available before the tests (no scoring)

- v2's walk-forward crisis exam F1 (trained to December 2007, projected 2008-2010): projected / actual
  defaults 0.38 under the recorded count, 0.50 counted correctly (L-035, L-039).
- v2's normal-period exams F2-F4, counted correctly: 1.27, 1.37, 1.35 (v2 over-projects defaults in
  2011-2019; L-039 re-measurement).
- v2 holdout (July 2024 - June 2026), counted correctly: 0.92; in expected-loss terms 0.74 at the
  model's LGD (L-038).
- The booked LGD predicts 1.64 times the real loss per default on resolved 2017-2021 defaults (L-033).
- Few defaults before 2007 have losses, so the crisis-backtest LGD is fitted on a thin sample.
