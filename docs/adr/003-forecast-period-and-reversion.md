# ADR-003: Forecast period and reversion

- **Status:** accepted, 14 November 2026; amended 15 November 2026 (timing of the Fed paths)
- **Related:** ADR-005 (scenario weighting), the Phase 5 sensitivity table

## Context

CECL (ASC 326) asks for lifetime expected losses using past experience, current conditions and
**reasonable and supportable forecasts**. Beyond the period a bank can forecast with support, it must
**revert** to historical loss information. The standard allows reverting immediately, on a
straight-line basis, or another systematic way. Many banks use 1 to 2 years of forecast, then revert
over about a year.

Mortgages run for decades, but most of a mortgage book's lifetime loss arrives in the first few
years, because many loans prepay. So the forecast and reversion choices move the reserve a lot.

What we have to forecast with:

- The **Fed's 2026 supervisory scenarios** give quarterly paths for unemployment, house prices,
  rates and more over 13 quarters, for the baseline and the severely adverse economy. That is enough
  to cover a 2-year forecast plus a 1-year reversion.
- **History** (FRED, FHFA, BLS) gives the long-run averages to revert to.
- The data's cut-off is **30 June 2026**, but the Fed's paths start earlier (Q1 2026). The forecast
  must start from the reserve date, so the first quarters of each path either replace actuals or are
  dropped (to settle in Phase 5).

The working plan in CLAUDE.md is a 2-year forecast, then a 1-year straight-line reversion,
configurable.

## Options

### A. How long the reasonable and supportable forecast lasts

| Option | For | Against |
|---|---|---|
| A1. 1 year | Most defensible; forecasts beyond a year are weak | Ignores the Fed's longer paths; the reserve reacts less to a downturn that is still building |
| **A2. 2 years** | Common in practice; uses the scenario paths where they matter most; matches the working plan | The second year is less certain |
| A3. 3 years | Uses the whole Fed path | Hard to call "supportable"; leaves no Fed quarters for reversion |

### B. How reversion happens

| Option | For | Against |
|---|---|---|
| B1. Immediate: jump to long-run averages when the forecast ends | Simplest | A cliff in the loss curve; overreacts to the forecast length |
| **B2. Straight-line over 1 year** | Smooth; common; matches the working plan; fits inside the 13 Fed quarters | The reversion length is itself a judgment (tested in the sensitivity table) |
| B3. Straight-line over 2 or more years | Even smoother | Longer reliance on the forecast; beyond the Fed paths |

### C. What reverts

| Option | For | Against |
|---|---|---|
| **C1. Input reversion:** the economic inputs (unemployment, house prices, rates) move to their long-run averages, and the PD, LGD and prepayment models keep running on those inputs | Keeps loan-level detail (each loan's age, LTV, credit score still matter); consistent with a PD x LGD x EAD engine; easy to explain | Long-run averages of inputs must be chosen and justified |
| C2. Output reversion: after the forecast, monthly default and loss rates revert to historical average loss rates by segment | Close to the wording of the standard ("historical loss information") | Throws away loan-level detail after year 2; needs a segment table; harder to combine with prepayment |

### D. The long-run averages

| Option | For | Against |
|---|---|---|
| D1. Full history in the data (1999 to 2025) | Includes a full cycle, crisis and recovery | Dominated by 2008; may be too pessimistic |
| D2. A longer economic history (for example 1990 onwards) from FRED / FHFA / BLS | More cycles; less dominated by one crisis | Inputs only: loans before 1999 aren't in Freddie's data |
| D3. Excluding 2008 to 2010 | Matches "normal" times | Hard to defend; the crisis is exactly what a reserve is for |

## Consequences

- The engine (`cecl.engine`, Phase 4) takes the forecast length, the reversion length and the
  long-run averages as configuration, not code. Changing them is an assumption change, logged with
  date, reason and approval.
- The Phase 5 sensitivity table shows the reserve with 1 / 2 / 3-year forecasts and 0 / 1 / 2-year
  reversion, so the effect of this choice is visible.
- Each quarter's allowance memo states the forecast length, reversion method and long-run values
  used.

## Decision

**Decision (Martins, 14 November 2026).**

**A. Forecast length: 2 years.** Common practice, and it makes good use of the Fed's paths. Three
years is hard to call "supportable"; one year ignores a downturn that is still building.

**B. Reversion: straight-line over 1 year.** Smooth, common and easy to explain. An instant jump
creates an odd cliff in the numbers.

**C. What reverts: the economic inputs (C1).** Unemployment, house prices and rates slide back to
their long-run averages, and the models keep running on each loan's own details.

*Why:* each loan's credit score, age and LTV keep mattering. The other option throws that detail away
after year 2.

**D. The long-run averages: a longer economic history (D2), from 1990 onward**, from FRED, FHFA and
BLS.

*Why:* 1990 onward covers four downturns (1990–91, 2001, 2008 and 2020), so "normal" isn't dominated
by 2008, and FRED, FHFA and BLS state data all reach back that far. Excluding the crisis would be hard
to defend, since a reserve exists precisely for crises.

**Timing.** The reserve date is 30 June 2026, when the data ends. Real data is used for January to
June 2026, and the Fed's path from July 2026. The Fed's quarter-by-quarter changes are applied
starting from the real June values, so there is no sudden jump. The 2-year forecast then runs to
mid-2028, still inside the Fed's paths.

**Amendment (Martins, 15 November 2026).** The Fed’s scenario paths are applied starting at the
reserve date: the Fed’s first projection quarter becomes the quarter after 30 June 2026 (Q3 2026).
The changes are applied from real June 2026 values. This keeps the full severity of the Fed’s shock
(about 10% unemployment and −30% house prices in the severe case), rather than losing the first
quarters because the real economy didn’t follow the Fed’s assumed early-2026 downturn.

*Effect, recorded 15 November 2026 (Phase 1 step 7, finding T):* the Fed's changes are measured
from its own starting point, 2025 Q4 (the last quarter of its published history file), and its 13
quarters land on 2026 Q3 to 2029 Q3. The severely adverse path now peaks at 9.7% unemployment
(2028 Q1) with house prices 29.7% below June 2026 at the worst (2028 Q2), against 7.0% and −19.7%
under the timing as first written. The first-written timing stays available in code (`calendar`)
only for the Phase 5 sensitivity table.

**Settings and point-in-time scenarios (20 November 2026, Phase 4 completion).** The forecast and reversion
lengths are now settings (`forecast_months`, `reversion_months`; defaults 24 and 12 as decided above),
passed to the projection job and to the pricing, which refuses to price a projection made with different
settings. Each reserve date uses only the Fed scenarios published by that date (`cecl.scenarios.fed_year`;
Martins, 20 November 2026): the 31 December 2025 reserve uses the Fed's 2025 scenarios (published
4 February 2025), measured from their own starting point (2024 Q4) with Fed quarter 1 landing on 2026 Q1;
the March and June 2026 reserves use the 2026 scenarios.
