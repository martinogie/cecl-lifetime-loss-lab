# Written expectations before the out-of-time test

Recorded **18 November 2026, before the out-of-time test has run** (ADR-009 E: the test months, July
2022 to June 2026, have never been scored). Committed so the expectation carries a timestamp earlier
than the test. It does not change the selection rule or the pass/fail criteria (ADR-009 Amendment 2
(a) and (b)); it says in advance what we expect to see, so the result can be read honestly.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.

## Martins's expectation (his words)

> In July 2022 to June 2026 (high rates, rate lock-in), payoffs may be over-predicted, especially
> no-incentive turnover.

## Why (the evidence on 2017-2019, scoring only)

- In 2017-2019, a strong housing market, the current-loan model **under-predicted** payoffs of
  seasoned loans with no refinance incentive (home sales and moves): predicted / actual 0.57 for
  vintages 2002-2004 and 2009-2013 with a rate incentive of zero or below, 65% of those vintages' missed
  payoffs; refinance payoffs (incentive over 1 point) were right (1.09). In 1999-2016 the same cells
  were calibrated (0.97). Limitations log L-031.
- Turnover moves with the housing market and with how freely owners can move. In 2022-2026 most
  owners hold rates well below the market rate (rate lock-in), so they move less. The model has no
  input for lock-in beyond the rate incentive, so the 2017-2019 under-prediction may **reverse** into
  an over-prediction of payoffs, especially for loans with no refinance incentive.
- Effect on defaults: if payoffs are over-predicted, loans leave the projection too early, so projected
  defaults would tend to be **too low**. That direction matters for the verdict, which is on defaults
  (Amendment 2 (a)). Payoff cells are reported but do not fail the test (Amendment 2 (b), L-028).

## Also expected (from the 2017-2019 results, for the record)

- Young loans (0-1 years old) rolled to 30 days late and paid off less than predicted in 2017-2019
  (1.24 and 1.44); this may differ again in 2022-2026 (L-031).
- Loans 60 days late at the start were projected below their actual defaults in 2017-2019 (L-030);
  the same direction is expected on the test.
