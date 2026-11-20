# Stress testing and sensitivity: 30 June 2026 book (Phase 5)

- **Book:** 350,495 Freddie Mac sample loans, $84,623.6m, alive at 30 June 2026.
- **Booked reserve:** performing $268.3m + defaulted $29.2m = total allowance $297.5m.
- **Source:** `docs/phase5/stress_2026Q2.json` (`scripts/build_stress.py`); decisions in `docs/phase5/plan.md`.

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

## Key findings (plain English)

1. **The Fed's severely adverse scenario multiplies the lifetime loss by about 10 times**: $95.5m under the baseline, $958.4m under severely adverse (1.13% of the book). Most of it arrives in the first 9 quarters ($828.7m).
2. **House prices drive the losses; unemployment much less.** House prices -30% on the baseline give $815.4m (8.5 times the baseline); unemployment +4 points gives $124.3m (1.3 times). Falling prices raise both the chance of default (less equity) and the loss per default (LGD tied to the current loan-to-value), while unemployment acts only through the chance of default. Together (-30% and +4) they give $1,047.0m, above the Fed's severely adverse.
3. **Speed matters:** the same -30% fall all at once in July 2026 gives $936.0m, 15% more than when it builds up over a year (information only).
4. **Mortgage-rate shocks barely move the reserve** ($-1.7m for +2 points, $-2.7m for -2 points). Lower rates mean more payoffs, so less exposure; higher rates keep loans on the book longer, and the lifetime chance of default rises, yet the reserve falls slightly. A likely reason, not yet verified: v2's rate inputs move defaults towards loans that lose little (seasoned, low loan-to-value). Not intuitive, so it is logged (L-043) for validation.
5. **The assumption that moves the reserve most is the scenario weights** (spread $201.5m between 80/15/5 and 40/40/20), then the L-038 default uplift (+34%: +$92.2m), the Fed timing ($-90.6m with the Fed's calendar quarters, which starts the scenario's recession after it has partly passed) and the forecast period ($-54.5m with 12 months instead of 24). Payoff speed (about ±5%), the LGD floor (+2% at 10%) and the reversion speed (under 1%) matter little.
6. **Benchmark:** our 9-quarter severely adverse loss rate is 1.19%, inside the range of the 32 banks in the Fed's 2026 stress test (0.9%-2.8%) and below their aggregate (1.5%), as expected for conforming, insured loans; section 4 explains the differences.
7. The booked reserve is unchanged: performing $268.3m, total allowance $297.5m. Phase 5 changes no booked figure; it shows how far the figure could move.

## 1. The Fed's scenarios, quarter by quarter

What it is: the 30 June 2026 book played forward under each scenario. Expected defaults and expected loss (chance of default x balance at default x loss per $ defaulted) are shown for each quarter after the reserve date. Unemployment and house prices are the book's state paths, weighted by balance; house prices are shown as the change since June 2026.

| Scenario | Losses, first 9 quarters | 9-quarter loss rate (of average balance) | Lifetime reserve | Lifetime loss rate | Lifetime chance of default |
|---|---|---|---|---|---|
| Baseline | $68.5m | 0.10% | $95.5m | 0.11% | 2.29% |
| Adverse (halfway path) | $325.0m | 0.47% | $383.9m | 0.45% | 3.62% |
| Severely adverse | $828.7m | 1.19% | $958.4m | 1.13% | 5.44% |

### Baseline

| Quarter | Unemployment | House prices vs June 2026 | Mortgage rate | Expected defaults (loans) | Expected loss | Loss rate (of start balance) |
|---|---|---|---|---|---|---|
| 2026 Q3 | 4.5% | 0.5% | 6.36% | 601 | $8.1m | 0.010% |
| 2026 Q4 | 4.5% | 0.8% | 6.29% | 623 | $8.4m | 0.010% |
| 2027 Q1 | 4.5% | 0.9% | 6.29% | 559 | $7.8m | 0.009% |
| 2027 Q2 | 4.4% | 1.1% | 6.26% | 437 | $6.8m | 0.008% |
| 2027 Q3 | 4.4% | 1.1% | 6.19% | 468 | $8.0m | 0.009% |
| 2027 Q4 | 4.3% | 1.2% | 6.16% | 509 | $8.6m | 0.010% |
| 2028 Q1 | 4.2% | 1.3% | 6.09% | 457 | $7.8m | 0.009% |
| 2028 Q2 | 4.2% | 1.3% | 6.09% | 371 | $6.2m | 0.007% |
| 2028 Q3 | 4.6% | 1.5% | 6.07% | 424 | $6.7m | 0.008% |
| 2028 Q4 | 4.9% | 2.0% | 6.05% | 508 | $7.4m | 0.009% |
| 2029 Q1 | 5.3% | 2.7% | 6.04% | 531 | $6.2m | 0.007% |
| 2029 Q2 | 5.7% | 3.7% | 6.02% | 489 | $5.2m | 0.006% |

### Adverse (halfway path)

| Quarter | Unemployment | House prices vs June 2026 | Mortgage rate | Expected defaults (loans) | Expected loss | Loss rate (of start balance) |
|---|---|---|---|---|---|---|
| 2026 Q3 | 5.4% | -3.9% | 6.31% | 629 | $15.1m | 0.018% |
| 2026 Q4 | 6.0% | -6.3% | 6.22% | 835 | $28.1m | 0.033% |
| 2027 Q1 | 6.4% | -7.8% | 6.17% | 909 | $35.7m | 0.042% |
| 2027 Q2 | 6.7% | -9.3% | 6.11% | 860 | $37.7m | 0.045% |
| 2027 Q3 | 6.9% | -10.7% | 6.02% | 1,062 | $47.3m | 0.056% |
| 2027 Q4 | 7.0% | -12.1% | 5.96% | 1,134 | $50.4m | 0.060% |
| 2028 Q1 | 7.0% | -13.4% | 5.87% | 998 | $45.4m | 0.054% |
| 2028 Q2 | 6.9% | -14.0% | 5.84% | 747 | $34.8m | 0.041% |
| 2028 Q3 | 6.6% | -13.4% | 5.88% | 726 | $30.4m | 0.036% |
| 2028 Q4 | 6.3% | -12.7% | 5.93% | 680 | $24.0m | 0.028% |
| 2029 Q1 | 6.0% | -11.9% | 5.97% | 531 | $13.4m | 0.016% |
| 2029 Q2 | 5.7% | -11.0% | 6.02% | 416 | $7.5m | 0.009% |

### Severely adverse

| Quarter | Unemployment | House prices vs June 2026 | Mortgage rate | Expected defaults (loans) | Expected loss | Loss rate (of start balance) |
|---|---|---|---|---|---|---|
| 2026 Q3 | 6.2% | -8.4% | 6.26% | 659 | $21.7m | 0.026% |
| 2026 Q4 | 7.4% | -13.5% | 6.16% | 1,071 | $57.5m | 0.068% |
| 2027 Q1 | 8.4% | -16.6% | 6.06% | 1,362 | $94.0m | 0.111% |
| 2027 Q2 | 9.1% | -19.6% | 5.96% | 1,353 | $104.6m | 0.124% |
| 2027 Q3 | 9.5% | -22.5% | 5.86% | 1,612 | $124.7m | 0.147% |
| 2027 Q4 | 9.8% | -25.3% | 5.76% | 1,814 | $128.5m | 0.152% |
| 2028 Q1 | 9.8% | -28.0% | 5.66% | 1,641 | $119.3m | 0.141% |
| 2028 Q2 | 9.6% | -29.4% | 5.59% | 1,316 | $98.5m | 0.116% |
| 2028 Q3 | 8.6% | -28.3% | 5.70% | 1,210 | $79.9m | 0.094% |
| 2028 Q4 | 7.6% | -27.4% | 5.80% | 973 | $55.2m | 0.065% |
| 2029 Q1 | 6.6% | -26.5% | 5.91% | 772 | $30.7m | 0.036% |
| 2029 Q2 | 5.7% | -25.7% | 6.02% | 567 | $14.3m | 0.017% |

## 2. Custom shocks (on top of the Fed baseline)

What it is: each shock is added to the Fed baseline. It builds up in a straight line over the first 4 quarters, stays in place to the end of the 24-month forecast, and then the usual reversion runs (ADR-003). House prices revert in growth, so their level stays lower; unemployment and the mortgage rate return to their long-run levels. Each shock is a fresh projection by model v2 under the shocked economy. The baseline reserve is $95.5m (no weighting; the booked reserve weights three scenarios).

| Shock | Worst house-price change | Peak unemployment | Reserve (lifetime) | Change vs baseline | Times baseline | 9-quarter loss rate |
|---|---|---|---|---|---|---|
| Fed baseline (no shock) | 0.5% | 5.7% | $95.5m | - | 1.00 | 0.10% |
| House prices -10% | -9.1% | 5.7% | $238.0m | $142.5m | 2.49 | 0.26% |
| House prices -20% | -19.2% | 5.7% | $461.3m | $365.8m | 4.83 | 0.48% |
| House prices -30% | -29.3% | 5.7% | $815.4m | $719.8m | 8.54 | 0.87% |
| Unemployment +2 points | 0.5% | 6.4% | $108.7m | $13.2m | 1.14 | 0.13% |
| Unemployment +4 points | 0.5% | 8.4% | $124.3m | $28.8m | 1.30 | 0.15% |
| Mortgage rate +2 points | 0.5% | 5.7% | $93.8m | $-1.7m | 0.98 | 0.08% |
| Mortgage rate -2 points | 0.5% | 5.7% | $92.8m | $-2.7m | 0.97 | 0.12% |
| House prices -20% and unemployment +2 | -19.2% | 6.4% | $531.7m | $436.2m | 5.57 | 0.64% |
| House prices -30% and unemployment +4 | -29.3% | 8.4% | $1,047.0m | $951.5m | 10.96 | 1.31% |
| House prices -30% at once in July 2026 (information only) | -29.6% | 5.7% | $936.0m | $840.5m | 9.80 | 1.02% |

## 3. Sensitivity table: how much each assumption moves the reserve

What it is: the booked performing-loan reserve ($268.3m, weighted 60/30/10) recomputed with one assumption changed at a time. The defaulted-loan reserve ($29.2m) does not depend on these assumptions and is added for the total allowance.

| Assumption | Setting | Performing reserve | Change | Change % | Total allowance |
|---|---|---|---|---|---|
| Forecast period | **24 months (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| Forecast period | 12 months | $213.8m | $-54.5m | -20.3% | $243.0m |
| Forecast period | 36 months | $267.5m | $-0.8m | -0.3% | $296.7m |
| Reversion speed | **12 months (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| Reversion speed | 6 months | $267.1m | $-1.2m | -0.4% | $296.3m |
| Reversion speed | 24 months | $268.6m | $0.3m | 0.1% | $297.8m |
| Fed timing | **shifted (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| Fed timing | calendar quarters | $177.7m | $-90.6m | -33.8% | $206.9m |
| Payoff speed | **x1.0 (as fitted) (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| Payoff speed | x0.8 | $280.7m | $12.4m | 4.6% | $309.9m |
| Payoff speed | x1.2 | $254.6m | $-13.7m | -5.1% | $283.8m |
| LGD floor (severity of loans that lose) | **none (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| LGD floor (severity of loans that lose) | 5% | $270.3m | $2.0m | 0.7% | $299.5m |
| LGD floor (severity of loans that lose) | 10% | $274.8m | $6.5m | 2.4% | $304.0m |
| Scenario weights (baseline/adverse/severe) | 80/15/5 | $181.9m | $-86.4m | -32.2% | $211.1m |
| Scenario weights (baseline/adverse/severe) | 70/20/10 | $239.5m | $-28.8m | -10.7% | $268.7m |
| Scenario weights (baseline/adverse/severe) | **60/30/10 (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| Scenario weights (baseline/adverse/severe) | 50/40/10 | $297.2m | $28.8m | 10.7% | $326.3m |
| Scenario weights (baseline/adverse/severe) | 50/35/15 | $325.9m | $57.6m | 21.5% | $355.1m |
| Scenario weights (baseline/adverse/severe) | 40/40/20 | $383.5m | $115.1m | 42.9% | $412.6m |
| Default uplift (L-038 Option A) | **0% (Option B) (booked)** | $268.3m | $0.0m | 0.0% | $297.5m |
| Default uplift (L-038 Option A) | +34% | $360.5m | $92.2m | 34.3% | $389.7m |

### Which assumption matters most (ranked by the spread between its lowest and highest setting)

| Rank | Assumption | Lowest change | Highest change | Spread |
|---|---|---|---|---|
| 1 | Scenario weights (baseline/adverse/severe) | $-86.4m | $115.1m | $201.5m |
| 2 | Default uplift (L-038 Option A) | $0.0m | $92.2m | $92.2m |
| 3 | Fed timing | $-90.6m | $0.0m | $90.6m |
| 4 | Forecast period | $-54.5m | $0.0m | $54.5m |
| 5 | Payoff speed | $-13.7m | $12.4m | $26.1m |
| 6 | LGD floor (severity of loans that lose) | $0.0m | $6.5m | $6.5m |
| 7 | Reversion speed | $-1.2m | $0.3m | $1.5m |

## 4. Benchmark: the Fed's 2026 stress test (DFAST), first-lien mortgages

| | 9-quarter severely adverse loss rate |
|---|---|
| **Our book** (Fed 2026 severely adverse path, 9 quarters from July 2026, % of average balance) | **1.19%** |
| Our book, % of the starting balance | 0.98% |
| DFAST 2026, 32 banks in aggregate | 1.5% |
| DFAST 2026, range across banks | 0.9% - 2.8% |
| DFAST 2026, examples | Wells Fargo 0.9%, JPMorgan Chase 1.2%, Bank of America 1.3%, Citigroup 2.0% |

Like for like: the same Fed scenario (2026 severely adverse), the same 9 scenario quarters (our path starts the Fed's quarter 1 in July 2026, the quarter after our reserve date), losses over average balances. Our rate is **inside** the banks' range.

Why the numbers differ (expected):
- **Losses at default vs charge-offs.** We count the expected loss when a loan defaults; banks report net charge-offs, which come later (foreclosure and sale take a year or more), so part of their losses from the same defaults falls after the 9 quarters.
- **Different books.** Bank mortgage books include jumbo, older and non-conforming loans; ours is Freddie Mac conforming loans, with mortgage insurance above 80% loan-to-value, which lowers losses.
- **Our LGD is conservative** (L-033: 1.64 times the real loss per default on 2017-2021 defaults), which pushes our rate up.
- **Model v2 failed its holdout** (L-038: defaults short by about 26% in loss terms in 2024-2026) and over-projects in calmer years (L-039); under severe stress its direction is not measured (the crisis exam failed, L-035).

Context: Freddie Mac's own allowance is 0.19% of its single-family loans at 30 June 2026; ours is 0.35% (allowance memo, section 5).

Source: Federal Reserve, 2026 Supervisory Stress Test results (24 June 2026), public_results_DFAST_2026.csv: domestic first-lien mortgage loss rate, severely adverse, 9 quarters (2026 Q1 - 2028 Q1), losses as a % of average balances.

## 5. Limits

- One reserve date (30 June 2026) and one book (Freddie Mac conforming sample). Shocks are applied to every state alike.
- Model v2 is `@challenger`, failed its holdout (L-038) and its crisis exam (L-035); stress results show how the model responds, not a validated loss forecast.
- After year 3 the loss runs at the month-36 chances and the long-run economy (L-040), so shocks that persist beyond 3 years are only partly reflected.
- Credit-loss part only; no revenue, expense or capital projections.
