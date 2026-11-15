# ADR-007: Sample weighting

- **Status:** accepted, 15 November 2026
- **Related:** ADR-001 (sample files; full data for chosen vintages later), L-019 (the sample is drawn
  equally by quarter), Phase 1 step 6 (comparison with Freddie's summary statistics), ADR-005
  (scenario weighting, a different kind of weight)

## Context

Freddie's sample files hold 50,000 loans per full origination year. The user guide describes them as
"a simple random sample", but every year has **exactly 12,500 loans in each origination quarter**
(found in step 6). Real lending volume varies a lot between quarters: refinance booms (2003, 2012,
2020–2021) and sudden slowdowns when rates jump (2022). So each sample loan stands for a different
number of real loans depending on its quarter, and a plain average over the sample gives
low-volume quarters too much weight.

**Where the effect shows (step 6):** our average **interest rate** is above Freddie's in 27 of 28
vintages, by up to 0.35 points. The gap is largest in years when rates moved sharply within the year
*and* volume shifted between quarters. In 2022, for example, rates rose from 3.6% (our Q1 sample
average) to 6.4% (Q4) while volume fell, so the quiet high-rate quarters count as much as the busy
low-rate ones. Other measures move less within a year, so the effect is smaller there and often
inside sampling error.

A **quarterly weight** would correct population statistics: for each origination quarter, the real
number of loans (or original balance) divided by the 12,500 sample loans. It needs each quarter's
true loan count and balance.

Two different questions use the sample:

1. **"What do all of Freddie's loans look like?"** Comparisons with Freddie's published figures, and
   any statistic presented as describing Freddie's whole book. These would need weights.
2. **"What is the reserve for our bank's book?"** In this project the sample loans *are* the book
   (ADR-001: "treated as our bank's mortgage book"). There is no wider population to represent, so the
   loans can be taken as they are.

**Martins' stated direction (15 November 2026):** don't fetch approximate weights. Document the
equal-by-quarter design and the differences it causes, with the effect shown where it is visible.
Models and the reserve use the sample as it is. When ADR-001's full-data vintages are loaded (2005–2008
for the crisis backtest, plus 2019 for COVID, optionally 2017), count loans by origination quarter for those years from
the loan-level data and show the weighted comparison then.

## Options

### A. Where weights are used

| Option | For | Against |
|---|---|---|
| A1. No weights; the design and its effects are documented, and every comparison with Freddie notes it (direction above) | Simplest; models and the reserve describe a concrete book exactly as loaded; nothing approximate | Comparisons with Freddie's population figures keep a known, explained gap until exact weights are available |
| A2. Weights for population statements only; the reserve's book is the sample as it is | Comparisons with Freddie close the gap | Needs each quarter's true counts for every year; approximate sources are ruled out |
| A3. Weights everywhere, including the reserve | The reserve describes a book shaped like Freddie's whole book | The "book" becomes an abstraction (1.4 million sample loans standing for 49 million); every model and table carries weights |

### B. Effect on model training (PD, prepayment, LGD)

| Option | For | Against |
|---|---|---|
| **B1. Train unweighted, with the economic conditions of each loan's time available to the model** (in line with the direction above) | The sample was drawn by quarter only, so *within* a quarter it is a fair random sample. A model that predicts a loan's risk from its own features (credit score, LTV, age, the economy at the time) is not distorted by how many loans each quarter contributed, as long as those conditions are in the model. Standard practice; every loan counts equally | A model's average prediction for a whole vintage reflects the sample's quarter mix, so vintage-level figures compared with Freddie inherit the same documented gap |
| B2. Train with quarterly weights | The fitted model reproduces Freddie's population averages directly | Needs exact weights for every year; adds noise to estimation; harder to explain |

**Validation** compares like with like: model predictions against the sample's own outcomes,
unweighted (does the model fit the loans it predicts?). Comparisons with Freddie's published population
figures always note the equal-by-quarter design.

### C. Exact quarterly counts later (when the full-data vintages are loaded)

For 2005–2008 and 2019 (optionally 2017), the full origination files give each quarter's exact loan count and
original balance. Step 6's comparison is then repeated for those years, weighted, to show how much of
the gap the quarter mix explained. Other years stay unweighted, with the design documented.

## Consequences

- No weights are stored or used in Phase 1. The panel, the models and the reserve use the 1,375,000
  sample loans as they are, and the allowance memo states plainly that the reserve is for that book.
- Every comparison with Freddie's published figures states that the sample is drawn equally by
  quarter, and which differences that explains.
- Weighted comparisons for the full-data years come later (Phase 6, with the crisis backtest).

## Decision

Decision (Martins, 15 November 2026): Freddie's sample takes 12,500 loans from every quarter, so plain
averages lean toward quieter quarters. This explains the interest-rate gap in step 6 (correlation +0.91
with each year's within-year rate movement). No approximate weights are used: the models and the
reserve use the 1,375,000 sample loans as they are, because our modelled "bank" owns exactly these
loans, and the models judge each loan on its own details and economic conditions. Any statistic
presented as describing all Freddie loans is labelled as unweighted. A weighted comparison will be added
when the full-data vintages (2005–2008, and 2019) are loaded, using exact quarterly counts from the
loan-level files.
