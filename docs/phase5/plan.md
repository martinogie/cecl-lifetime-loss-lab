# Phase 5 plan: stress testing and sensitivity

**Status (20 November 2026): proposed, waiting for Martins's decisions (below). Nothing built.**

Educational model on Freddie Mac conforming mortgages (sample book); not an audited reserve and not
accounting advice. Model v2 failed its holdout (L-038), and the check that measured it was biased
(L-039). There is no L-038 overlay (Option B). Every stress output states all three.

## What Phase 5 must show (roadmap v2, Section 7)

1. Fed 2026 baseline and severely adverse runs, with **quarterly results**.
2. Custom shocks, and the **sensitivity table** including the scenario weights.
3. Stress loss rates compared with **published benchmarks**, with the differences explained.

The book is the 30 June 2026 reserve book (350,495 loans, $84.6bn). Model v2, the Phase 3 LGD and
EAD, and the Phase 4 engine are reused unchanged.

## 1. Fed scenarios, quarter by quarter

We already have each loan's monthly default chance for 36 months under each Fed path (Phase 4). So
quarterly results only need the pricing step, with no new projection:
- for each quarter after 30 June 2026: expected defaults (count and balance), expected loss
  (PD x EAD x LGD) and the loss rate (loss / starting balance);
- the cumulative 9-quarter loss rate (the DFAST horizon, July 2026 to September 2028) and the lifetime
  loss;
- the paths themselves: unemployment, house prices and the mortgage rate, national and the top states.

## 2. Custom shocks (roadmap: house prices -10/-20/-30%, unemployment +2/+4 points, rates up or down)

Proposal: each shock is applied on top of the Fed baseline. It is reached in a straight line over the
first 4 quarters, held through the 24-month forecast, then reverted to the long-run values (ADR-003),
like the Fed paths. House-price shocks apply to every state's index, unemployment shocks to every
state's rate, and rate shocks to the mortgage rate (±2 points). That gives 7 shocks plus 2 combined
ones: house prices -20% with unemployment +2, and house prices -30% with unemployment +4. The
combinations echo the downturn check (L-037).

Each shock needs a new projection, because v2's default and payoff chances depend on the economy: one
Databricks run for the 9 shocks on the June book, about 10 minutes.

## 3. Sensitivity table (how much the reserve moves for each assumption)

| Assumption | Values tested (booked value in bold) | How |
|---|---|---|
| Scenario weights (ADR-005) | severe 5 / **10** / 15 / 20 / 25%; also 50/40/10 and 70/20/10 | pricing only (already in Phase 4) |
| Forecast period (ADR-003) | 12 / **24** / 36 months | re-projection (new setting) |
| Reversion speed | 6 / **12** / 24 months | re-projection |
| Fed timing (ADR-003 amendment) | **shifted** / calendar | re-projection (the code exists) |
| Prepayment speed | payoff chances x 0.8 / **1.0** / 1.2 | re-projection with a payoff multiplier (new option in the simulation) |
| LGD floor | **none** / 5% / 10% severity floor on loans that lose | pricing only |
| Default uplift (L-038 Option A) | **0%** / +34% | pricing only (already in Phase 4) |

Re-projections: forecast 12 and 36, reversion 6 and 24, calendar timing, and prepayment 0.8 and 1.2,
each under 3 scenarios. That is 7 x 3 = 21 runs, about 25 Databricks minutes.

## 4. Published benchmarks

| Benchmark | Figure | Source |
|---|---|---|
| Fed DFAST 2026, severely adverse: domestic first-lien mortgage losses over 9 quarters, as a % of average balances | **1.5%** for the 32 banks in aggregate (range 0.9-2.8%; for example JPMorgan 1.2%, Wells Fargo 0.9%, Citigroup 2.0%) | `public_results_DFAST_2026.csv` and the 2026 DFAST results (24 June 2026), federalreserve.gov |
| Freddie Mac's own allowance ratio | 0.19% at 30 June 2026 (in the memo) | Freddie Mac Q2 2026 earnings release |

Our comparable figure is the 9-quarter severely adverse loss rate (part 1). The memo will explain the
expected differences:
- DFAST losses are banks' net charge-offs; ours are expected losses at default;
- bank books include jumbo, older and non-conforming loans;
- Freddie loans carry mortgage insurance above 80% LTV;
- our LGD is conservative (L-033, 1.64x).

## Outputs

- `docs/phase5/stress_2026Q2.json` (aggregates only) and `docs/phase5/stress_report.md`: quarterly
  tables, shocks, the sensitivity table (a "tornado" ranking of which assumption moves the reserve
  most, which answers roadmap Q4), and the benchmark comparison.
- New code with tests: shock paths (`cecl/scenarios.py`), a payoff multiplier (`cecl/models/simulate.py`),
  an LGD floor and quarterly aggregation (`cecl/engine.py`), and `scripts/build_stress.py`.
- Change log rows, and limitation entries if new limits appear.

## Compute and timing

About 35 Databricks minutes in total (two runs), plus local pricing (about 1-2 hours on the PC, cached).
Never on a Monday before 13:00 or between 02:30 and 04:00 (enforced by `scripts/run_job.py`).

## Decisions needed from Martins before building

1. **Shock design:** on top of the Fed baseline, reached over 4 quarters, held for the 24-month
   forecast, then reverted (proposal). The alternative is an immediate jump at July 2026.
2. **The two combined shocks** (house prices -20% with unemployment +2; house prices -30% with
   unemployment +4): keep, change or drop.
3. **Sensitivity ranges** in the table above, especially prepayment x0.8 / x1.2 and the LGD floors of
   5% and 10%.
4. **Benchmark:** DFAST 2026 severely adverse first-lien 9-quarter loss rate (1.5% aggregate,
   0.9-2.8%) as the main published comparison; the Freddie ratio stays as context.
