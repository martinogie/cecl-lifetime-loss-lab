# Phase 6 validation results

Rules and pass/fail tests committed before any test ran: `docs/phase6/test_designs.md`. Martins's written expectations, committed before the tests: `docs/phase6/backtest_expectations.md`. Each test ran once. Information for the validator: no booked figure, model status or overlay decision changes.

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

## 1. Crisis backtest (models fitted to December 2006, tested on 2007-2010)

Book: 188,320 loans alive at 31 December 2006 ($26,752.8m). PD and payoffs: the v2 recipe refitted on months up to December 2006. LGD refitted on 3,359 defaults resolved by then (890 with a loss; 5.4% lost per $ defaulted). 48 months on the economy as it happened.

| | Projected | Actual | Projected / actual | Benchmark / actual |
|---|---|---|---|---|
| Defaults | 5,564 | 10,698 | **0.52** | 0.45 |
| Loss | $55.4m | $538.9m | **0.10** | 0.06 |

| Year | Projected defaults | Actual | Ratio | Projected loss | Actual loss | Ratio |
|---|---|---|---|---|---|---|
| 2007 | 1,086 | 1,403 | 0.77 | $8.9m | $51.0m | 0.17 |
| 2008 | 1,308 | 2,204 | 0.59 | $13.3m | $126.3m | 0.11 |
| 2009 | 1,772 | 3,962 | 0.45 | $18.9m | $214.5m | 0.09 |
| 2010 | 1,398 | 3,129 | 0.45 | $14.2m | $147.1m | 0.10 |

Pass rule: defaults in 0.80-1.25 (no), loss in 0.80-1.25 (no), closer than the benchmark on both (**yes**). **Result: FAIL.** Share of actual loss valued at the booked LGD (defaults unresolved by June 2026): 17.1%.

## 2. Rolling backtest (year-ends 2012-2022, logistic PD and LGD refitted at each date)

Projected expected loss over the next 36 months against the actual losses on the same book from defaults in those 36 months. Economy (a): as it happened; (b): flat then reverted (information).

| Date | Loans | LGD refit: loss per $ | (a) projected | (a) actual | (a) ratio | (b) ratio | Lifetime reserve (a) | Defaults ratio (a) | Unresolved share |
|---|---|---|---|---|---|---|---|---|---|
| 2012 | 228,274 | 32.1% | $152.8m | $136.8m | **1.12** | 1.96 | $168.6m | 1.42 | 20% |
| 2013 | 228,049 | 32.0% | $94.9m | $88.4m | **1.07** | 1.32 | $109.2m | 1.36 | 20% |
| 2014 | 250,049 | 32.2% | $73.4m | $61.7m | **1.19** | 1.11 | $83.5m | 1.46 | 23% |
| 2015 | 265,597 | 32.0% | $60.3m | $43.2m | **1.40** | 1.65 | $69.1m | 1.42 | 24% |
| 2016 | 273,564 | 31.3% | $50.5m | $30.0m | **1.68** | 1.81 | $61.2m | 1.34 | 26% |
| 2017 | 290,549 | 30.2% | $54.6m | $21.2m | **2.57** | 2.35 | $69.8m | 2.01 | 29% |
| 2018 | 309,942 | 29.4% | $50.8m | $14.0m | **3.62** | 3.25 | $54.2m | 2.60 | 30% |
| 2019 | 317,446 | 28.6% | $43.0m | $7.9m | **5.48** | 5.44 | $44.9m | 3.02 | 24% |
| 2020 | 276,691 | 27.9% | $10.8m | $6.2m | **1.73** | 4.19 | $12.9m | 1.57 | 21% |
| 2021 | 248,691 | 26.9% | $6.7m | $8.1m | **0.83** | 0.41 | $9.6m | 0.90 | 40% |
| 2022 | 268,439 | 26.3% | $17.6m | $17.0m | **1.03** | 0.57 | $22.0m | 0.95 | 68% |

Economy (a): pooled ratio **1.42** (bar 0.80-1.25: no); dates inside 0.67-1.50: 6 of 11 (need 8: no). **FAIL** (judged).

Economy (b): pooled ratio **1.74** (bar 0.80-1.25: no); dates inside 0.67-1.50: 2 of 11 (need 8: no). **FAIL** (information).

## 3. Forbearance treatment test (2020-2021)

Defaults January 2020 - December 2021, booked definition (A, the stop-the-clock rule): 2021: 684, 2020: 893. With the forbearance flag ignored (B): 3,672, of which **2,353 only under B**; of those, **1,408 (60%) were current again within 12 months** and 48 later had a loss.

| Book alive at 30 June 2020, 36 months | Loans | Projected loss | Actual loss | Ratio |
|---|---|---|---|---|
| (A) booked: stop-the-clock | 304,015 | $30.7m | $5.8m | **5.32** |
| (B) flag ignored | 303,219 | $106.1m | $5.9m | **17.98** |

Pass rule: (A) closer to 1 than (B) (**yes**); at least half the extra defaults cured within 12 months (**yes**). **Result: PASS (the stop-the-clock rule is supported).**

## 4. Peer comparison (Q9): residential real estate allowance ratios, banks with $1bn+ in assets

FFIEC call reports, Schedule RI-C item 1.c (residential real estate loans as each bank segments its allowance: 1-4 family first and junior liens and home-equity lines; some banks include multifamily). A sanity range, not a target.

| 30 June 2026 | Banks | 10th pct | 25th pct | Median | 75th pct | 90th pct | Pooled |
|---|---|---|---|---|---|---|---|
| All banks $1bn+ | 949 | 0.38% | 0.59% | **0.88%** | 1.20% | 1.50% | 0.52% |
| $1-10bn | 794 | 0.42% | 0.63% | **0.91%** | 1.21% | 1.50% | 0.87% |
| $10-100bn | 119 | 0.19% | 0.47% | **0.83%** | 1.23% | 1.52% | 0.75% |
| $100bn+ | 26 | 0.15% | 0.24% | **0.44%** | 0.54% | 0.84% | 0.35% |
| Under $1bn now (filing on last year's assets) | 10 | 0.55% | 0.64% | **0.90%** | 1.21% | 1.68% | 0.87% |

| Our ratios and Freddie's | Ratio | Against the banks' middle 50% |
|---|---|---|
| Our total allowance | 0.35% | below |
| Our baseline only | 0.15% | below |
| Our total, LGD conservatism removed (/1.64) | 0.21% | below |
| Freddie Mac (Q2 2026 release) | 0.19% | below |

Over time (all banks $1bn+, median and pooled; CECL / incurred-loss banks):

| Period | Banks | Median | Pooled | Banks by method |
|---|---|---|---|---|
| 2020-12-31 | 724 | 0.90% | 0.79% | {'cecl': 377, 'incurred': 347} |
| 2021-12-31 | 723 | 0.80% | 0.55% | {'cecl': 412, 'incurred': 311} |
| 2022-12-31 | 879 | 0.82% | 0.56% | {'cecl': 540, 'incurred': 339} |
| 2023-12-31 | 900 | 0.89% | 0.53% | {'cecl': 900} |
| 2024-12-31 | 930 | 0.88% | 0.51% | {'cecl': 930} |
| 2025-12-31 | 936 | 0.88% | 0.53% | {'cecl': 936} |
| 2026-06-30 | 949 | 0.88% | 0.52% | {'cecl': 949} |
| 2026-09-30 | 534 | 0.86% | 0.50% | {'cecl': 534} |

Why ours differs: bank books include jumbo, non-conforming and home-equity loans and are on banks' own models; Freddie loans above 80% LTV carry mortgage insurance; our LGD is conservative (L-033, 1.64x); our scenario weights put 79% of the performing reserve on the adverse and severely adverse paths. Before 2023 many banks still used the incurred-loss method, which reserves less than CECL; the table shows the split.

## 5. L-043: where the mortgage-rate shocks move the reserve

Market rate June 2026: 6.49%.

| Run | Reserve | Lifetime chance of default | Expected payoffs, 36 months | Exposure at default |
|---|---|---|---|---|
| baseline | $95.5m | 2.29% | 112,815 | $1,776.7m |
| rates_up2 | $93.8m | 2.41% | 77,996 | $1,856.7m |
| rates_down2 | $92.8m | 2.07% | 173,393 | $1,623.6m |

By current ltv band:

| Group | Balance | Reserve: base / +2 / -2 | Lifetime PD: base / +2 / -2 | Loss per $ defaulted: base / +2 / -2 |
|---|---|---|---|---|
| 60-80 | $27,444.5m | $46.5m / $46.1m / $44.0m | 2.19% / 2.32% / 1.92% | 8.2% / 7.8% / 8.8% |
| 80-90 | $10,445.9m | $21.6m / $20.7m / $21.7m | 3.46% / 3.59% / 3.26% | 6.3% / 5.8% / 6.6% |
| <=60 | $41,070.3m | $13.9m / $14.2m / $13.2m | 1.76% / 1.86% / 1.59% | 2.2% / 2.1% / 2.3% |
| >90 | $5,662.9m | $13.4m / $12.8m / $13.9m | 4.47% / 4.61% / 4.13% | 5.6% / 5.2% / 6.2% |

By vintage group:

| Group | Balance | Reserve: base / +2 / -2 | Lifetime PD: base / +2 / -2 | Loss per $ defaulted: base / +2 / -2 |
|---|---|---|---|---|
| 2018 and older | $10,014.6m | $2.4m / $2.5m / $2.3m | 2.13% / 2.29% / 1.98% | 1.3% / 1.3% / 1.4% |
| 2019-2021 | $18,813.4m | $5.1m / $5.0m / $4.5m | 1.80% / 1.84% / 1.63% | 1.7% / 1.7% / 1.7% |
| 2022-2024 | $32,153.1m | $50.8m / $49.7m / $50.7m | 2.69% / 2.86% / 2.50% | 6.2% / 5.8% / 6.6% |
| 2025-2026 | $23,642.5m | $37.3m / $36.6m / $35.2m | 2.20% / 2.30% / 1.89% | 7.6% / 7.2% / 8.3% |

By note rate vs market:

| Group | Balance | Reserve: base / +2 / -2 | Lifetime PD: base / +2 / -2 | Loss per $ defaulted: base / +2 / -2 |
|---|---|---|---|---|
| -0.5 to 0.5 | $27,410.8m | $37.8m / $38.3m / $37.4m | 2.16% / 2.34% / 1.94% | 6.8% / 6.4% / 7.4% |
| -2 to -0.5 | $18,285.5m | $15.8m / $16.9m / $16.1m | 2.17% / 2.45% / 1.99% | 4.4% / 4.2% / 4.8% |
| 0.5 to 1.5 | $10,088.0m | $31.2m / $28.3m / $29.9m | 4.01% / 3.96% / 3.71% | 8.0% / 7.4% / 8.3% |
| <= -2 | $28,436.8m | $7.8m / $7.6m / $6.9m | 1.82% / 1.83% / 1.63% | 1.7% / 1.7% / 1.7% |
| >= 1.5 | $402.4m | $2.8m / $2.7m / $2.4m | 6.36% / 6.66% / 5.71% | 11.4% / 10.4% / 11.0% |
