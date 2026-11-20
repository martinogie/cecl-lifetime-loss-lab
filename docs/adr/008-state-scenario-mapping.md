# ADR-008: State scenario mapping

- **Status:** accepted, 15 November 2026
- **Related:** ADR-003 (forecast period, reversion, timing), ADR-005 (scenario weighting, the
  halfway adverse path), Phase 1 step 7 (economic data, monthly fill rule M1), Phase 5 (stress
  testing), L-007 (adverse path built by the project)

## Context

The PD, prepayment and LGD models use **state** inputs: each loan's current LTV moves with its
state's house prices, and the PD model uses the state unemployment rate. But the Federal Reserve's
2026 supervisory scenarios (baseline and severely adverse) give **national** paths only:
13 quarters, 2026 Q1 to 2029 Q1, for the unemployment rate, a national house price index, interest
rates and more. The Fed doesn't publish state paths. Commercial providers sell state scenarios, but
that costs money and isn't reproducible by a reader. So the project needs a written, repeatable
rule that turns one national path into 51 state paths (50 states and DC).

What history shows (public FRED and FHFA data, computed 15 November 2026; year-over-year changes by
quarter, 1991–2019, each state's change regressed on the national change):

| Measure | Median state sensitivity ("beta") | Range | Typical fit (median R²) |
|---|---|---|---|
| Unemployment rate (points) | 0.87 | 0.23 (ND) to 1.38 (MI); 10th–90th percentile 0.57–1.16 | 0.78 |
| House prices (log change, FHFA all-transactions) | 0.74 | 0.20 (ND) to 2.50 (NV); 10th–90th percentile 0.37–1.42 | 0.61 (13 states below 0.5) |

- **States differ a lot, especially in house prices.** Nevada, Florida, Arizona and California have
  moved about twice as much as the nation; North Dakota and Texas far less.
- **The median is below 1** because the national series is dominated by large, volatile states
  (California, Florida). A plain average of state betas doesn't reproduce the national path.
- **History helps, but only partly.** Betas fitted on 1991–2006 predicted the 2007–2011 state house
  price falls with a mean error of 0.122 log points, against 0.137 for "every state moves like the
  nation". In 2008 the high-beta states fell even further than their betas said (Nevada about 4
  times the national fall, against a beta of 2.5).
- Including 2020–2026 changes the unemployment betas (COVID hit tourism states hardest: Nevada's
  beta rises from 1.18 to 1.80), so the estimation window matters.

Two more facts shape the rule:

- **The Fed's house price index is not FHFA's.** The Fed's scenario uses its own national index
  (level about 325 in 2026 Q1); our state data are FHFA all-transactions indexes. So only the Fed
  path's **percentage changes** carry over, never its levels.
- **Timing (ADR-003, as amended 15 November 2026):** real data run to June 2026; the Fed's path is
  shifted to the reserve date (its first quarter becomes 2026 Q3), as changes from the Fed's own
  starting point (2025 Q4), added to each state's real June 2026 value.

## Options

### A. How a state's path follows the national path

| Option | How | For | Against |
|---|---|---|---|
| A1. Uniform | Every state gets the national change: unemployment +X points, house prices −Y% | Simplest; no estimation; exactly reproduces the Fed's national change | Ignores the strongest pattern in the data: Nevada and North Dakota would fall by the same 30% in the severe scenario; understates losses in high-beta states, overstates in low-beta ones |
| A2. Historical sensitivity (beta) | State change = beta × national change, beta estimated per state from history (as in the table above) | Standard practice when only national paths exist; uses public data; each state's beta is a number a reader can check | Estimation error (worst for small states); a past pattern may not repeat; the window choice moves the betas |
| A3. Replay one past episode | Scale each state by its share of the 2007–2011 (or 2020) move relative to the nation | Easy to explain ("the 2008 pattern") | One episode only; 2008 was a housing boom-and-bust story that needn't repeat; gives negative or extreme multipliers (North Dakota −0.74, Nevada 4.2 for house prices) |
| A4. Buy state scenarios | A vendor's state paths | Professional, internally consistent | Costs money (hard rule 5); not reproducible by readers |

### B. If A2: how the betas are estimated and guarded

| Choice | Options | Comment |
|---|---|---|
| B1. Window | (a) 1991–2019, before COVID; (b) 1991 to the latest quarter | (a) keeps the lockdown pattern out of a recession that isn't a pandemic; (b) uses all the data. ADR-003 uses 1990 onward for long-run averages, so both start there (the first year-over-year change is 1991) |
| B2. Change measured | Year-over-year changes by quarter (unemployment in points, house prices in log change) | Year-over-year changes also remove the seasonality in FHFA's not-seasonally-adjusted state indexes |
| B3. Guardrails | (a) none; (b) shrink each beta toward the national average in proportion to its uncertainty, then cap it, e.g. unemployment 0.25–2.0 and house prices 0.25–3.0 | (b) stops a noisy small state from getting an extreme path, and never lets a state move against the nation |
| B4. National consistency | (a) none; (b) after mapping, rescale the state changes so their weighted average (weights: the book's balance by state) equals the Fed's national change | (b) keeps the book-level economy faithful to the Fed's scenario; (a) lets the book's state mix drive it, which is the point of state detail but can drift from the national path |

### C. Which variables are mapped by state

| Variable | Options |
|---|---|
| Unemployment rate | by state (as A), national path only as input |
| House prices | by state (as A) |
| Interest rates, mortgage rate | national for all states (rates are set nationally); no mapping |

### D. The adverse path and reversion (follow from ADR-003 and ADR-005)

- **Adverse:** ADR-005 builds it halfway between the national baseline and severely adverse paths,
  quarter by quarter; it is then mapped to states by the same rule as the other two.
- **Reversion:** after the 2-year forecast, each **state** reverts over 1 year to its **own**
  long-run average from 1990 onward (ADR-003 D2), not to the national average.
- **Monthly values:** state quarterly paths become monthly with the same rule as the history (step
  7, rule M1), so the history and the forecast join without a seam.

## Consequences

- `cecl.scenarios` (Phase 4/5) implements the rule; inputs are the national Fed paths and the state
  history built in step 7 (`silver/macro`). The betas, their window and the guardrails are
  configuration, logged as an assumption change if they change (control C09).
- The Phase 5 sensitivity table adds a row: the reserve under the chosen mapping and under uniform
  mapping (A1), so readers see how much state detail matters.
- Every allowance memo states the mapping, the window and the beta range used.
- A new limitations-log entry records that state paths are the project's construction, not the
  Fed's (like L-007 for the adverse path).
- The estimates are refreshed yearly with the Fed's new scenarios (February), alongside the ADR-005
  weight review.

## Decision

**Decision (Martins, 15 November 2026).**

State paths are built by scaling the Fed’s national changes by each state’s historical sensitivity
to the national series (estimated from 1991 onward), with limits on extreme values. This keeps the
national scenario intact while reflecting that some states, such as Nevada, swing much harder than
others, such as North Dakota. The sensitivities and limits are recorded with each run.

**Note (Martins, 19 November 2026, confirming before `cecl.scenarios` was built):** option B4b is confirmed:
after mapping the Fed's national changes to states by beta (window 1991 to the latest quarter, year-over-year
changes, caps 0.25-2.0 for unemployment and 0.25-3.0 for house prices), the state changes are rescaled so
their book-balance-weighted average equals the Fed's national change.
