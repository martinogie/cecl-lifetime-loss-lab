# Written expectations before the out-of-time test

- **Status:** final, confirmed by Martins (18 November 2026)

Recorded **18 November 2026, before the out-of-time test has run** (ADR-009 E: the test months, July
2022 to June 2026, have never been scored). Committed so the expectation carries a timestamp earlier
than the test. It does not change the selection rule or the pass/fail criteria (ADR-009 Amendment 2
(a) and (b)); it says in advance what we expect to see, so the result can be read honestly.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.

## Martins's expectation on payoffs (his words)

> In July 2022 to June 2026 (high rates, rate lock-in), payoffs may be over-predicted, especially
> no-incentive turnover.

## Martins's expectation on defaults (his words)

### My expectation on defaults
I expect the model to predict more defaults than actually happen for loans that were current in July 2022, probably 10–20% too many. Two reasons. First, 2022–2026 was a high-rate period, so borrowers kept their cheap loans and paid off less than the model expects; the turnover drift (L-031) and the young-loan over-prediction seen in validation (1.10 and 1.17) point to too many defaults for current loans. Second, house prices stayed high, so borrowers in trouble could often sell rather than default, which lowers actual defaults.

For loans 30 or 60 days late in July 2022, I expect the model to predict too few defaults, especially after the first three months, because it only remembers one month of history and misses loans that keep struggling (L-030).

Overall, the two errors may partly cancel, so the total could look close to actual while the groups are off. I think the model may fail the default vintage bar (1.25), as vintage 2016 was already at 1.27 in validation. If it fails, the models stay @challenger and the fix belongs in the next version, validated on a new period.

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
