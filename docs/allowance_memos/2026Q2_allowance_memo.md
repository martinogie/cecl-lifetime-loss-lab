# Allowance memo: Q2 2026 (reserve date 30 June 2026)

- **Prepared by:** Martins Ogiekpolor (maker). **Checker review:** _pending (a different day, ADR-006)._
- **Book:** Freddie Mac Single-Family sample loans alive at the reserve date. **Scenarios:** Federal Reserve 2026 baseline and severely adverse, plus the halfway adverse path (ADR-005), from July 2026.
- **Source:** `docs/phase4/reserve_2026Q2.json` (`scripts/build_reserve.py`); loan-level data never leaves the PC or Databricks.

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

## 1. The reserve

| | 31 March 2026 | 30 June 2026 |
|---|---|---|
| Loans | 341,291 | 350,495 |
| Balance | $81,024.4m | $84,623.6m |
| Performing-loan reserve (engine, weighted 60/30/10, no overlay) | $262.7m | $268.3m |
| Performing reserve ratio (reserve / balance) | 0.324% | 0.317% |
| Defaulted, unresolved loans (90+ days late or REO): loans / balance | 1,953 / $467.3m | 2,059 / $507.9m |
| Defaulted-loan reserve (balance x Phase 3 LGD) | $27.4m | $29.2m |
| **Total allowance (booked)** | **$290.1m** | **$297.5m** |
| Total allowance ratio | 0.356% | 0.349% |
| Sensitivity: total with the +34% default uplift on the performing reserve (Option A) | $380.3m | $389.7m |
| Tie-out: book reserve = sum of loan reserves (every scenario) | pass | pass |

### By scenario, 30 June 2026

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

| Scenario | Reserve | Of which: forecast (yr 1-2) / reversion (yr 3) / tail | Balance-weighted lifetime PD | With +34% uplift |
|---|---|---|---|---|
| Baseline | $95.5m | $61.7m / $25.6m / $8.1m | 2.29% | $128.3m |
| Adverse (halfway path) | $383.9m | $294.6m / $75.4m / $13.9m | 3.62% | $515.8m |
| Severely adverse | $958.4m | $748.7m / $180.2m / $29.5m | 5.44% | $1,287.6m |
| **Weighted 60/30/10 (booked)** | **$268.3m** | | | $360.5m |

### By scenario, 31 March 2026

| Scenario | Reserve | Of which: forecast (yr 1-2) / reversion (yr 3) / tail | Balance-weighted lifetime PD | With +34% uplift |
|---|---|---|---|---|
| Baseline | $94.8m | $64.8m / $23.1m / $6.9m | 2.33% | $127.4m |
| Adverse (halfway path) | $377.1m | $300.4m / $65.5m / $11.2m | 3.63% | $506.7m |
| Severely adverse | $927.1m | $741.3m / $158.0m / $27.8m | 5.53% | $1,245.5m |
| **Weighted 60/30/10 (booked)** | **$262.7m** | | | $353.0m |

### Severely adverse, with and without the uplift (Martins's requirement)

The LGD conservatism (L-033) was measured on resolved 2017-2021 defaults, good years; it may not hold in a downturn, so the severely adverse reserve is shown both ways:

| Date | Severely adverse, no uplift | Severely adverse, +34% uplift |
|---|---|---|
| 31 March 2026 | $927.1m | $1,245.5m |
| 30 June 2026 | $958.4m | $1,287.6m |

### Scenario-weight sensitivity (ADR-005: severe weight floor 5%, cap 25%; baseline and adverse keep 2:1)

| Severe weight | Reserve 30 June 2026 | With +34% uplift |
|---|---|---|
| 5% | $230.0m | $309.0m |
| 10% | $268.3m | $360.5m |
| 15% | $306.7m | $412.0m |
| 20% | $345.0m | $463.5m |
| 25% | $383.3m | $515.0m |

## 2. Overlay decision (ADR-006) and what it rests on

- **L-038 default shortfall, sized on expected loss** (`docs/phase4/overlay_sizing_L038.md`): over the 24 holdout months, projected expected loss was 0.74 of the same loans' actual defaults priced at the model's LGD, so +34% would close it.
- **L-033 LGD conservatism:** the LGD model predicts 1.64 times the real loss per default (resolved 2017-2021 defaults). Priced at the real loss, the projected expected loss is 1.22 times the estimated real loss.
- **Decision (Option B):** "The L-038 default shortfall is assessed at the expected-loss level. Because the LGD model's documented conservatism (L-033, 1.64x) more than offsets the default shortfall (0.74 in loss terms), the baseline reserve carries no L-038 overlay. Both errors are disclosed with every reserve figure. This applies to the baseline only and is reviewed each quarter." (Martins Ogiekpolor, 19 November 2026)
- **The 24-month gap may understate the lifetime gap:** 2026 was the weakest year of the holdout (0.74 of actual defaults counted correctly; 0.60 in loss terms), and the reserve runs to each loan's maturity.
- **Other context (L-039 re-measurement):** in 2011-2019 v2 over-projected defaults by 27-37%; so in benign conditions the default side is likely conservative too, and in a 2024-2026-like period it is short.
- Overlays booked: **none**. Cap check (5% each, 10% total): not applicable.

## 3. Rollforward, Q2 2026 (to the cent)

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

| Loan group (origination year) | 31 March 2026 | Provision | Gross write-offs | Recoveries | 30 June 2026 |
|---|---|---|---|---|---|
| 2018 and older | 10184581.55 | -403057.53 | -57907.84 | 0.00 | 9723616.18 |
| 2019-2021 | 19876616.09 | -1604611.24 | -58305.02 | 0.00 | 18213699.83 |
| 2022-2024 | 173696679.84 | -10223269.13 | -749362.14 | 0.00 | 162724048.57 |
| 2025-2026 | 86329746.48 | 20506392.98 | 0 | 0 | 106836139.46 |
| **Total** | **290087623.96** | **8275455.08** | **-865575.00** | **0.00** | **297497504.04** |

Reconciles to the cent: **yes** (start + provision - write-offs + recoveries = end, on the total allowance; provision is the balancing figure). Loss dispositions in the quarter: 57, of which 57 **estimated**: Freddie reports losses about 3 months after the sale; Q2 2026 write-offs not yet reported are estimated (balance removed x Phase 3 LGD), trued up at the Q3 close. Write-offs are Freddie's realised losses at disposition (a gain counts as zero); recoveries are MI and other recoveries.

## 4. Reserve bridge, 31 March to 30 June 2026 (weighted)

| Step | Amount |
|---|---|
| Start | $290.1m |
| Exits | $-14.0m |
| New loans | $26.4m |
| Credit quality and ageing | $14.3m |
| Economic forecast | $-21.1m |
| Model changes | $0.0m |
| Overlays | $0.0m |
| Defaulted loan reserve change | $1.8m |
| End | $297.5m |

Bridge reconciles: **yes**. Exits: the March reserve of loans that left the book; new loans: the June reserve of loans not in the March book; credit quality and ageing: continuing loans under the March economy, June vs March; economic forecast: the June economy vs the March economy for the June book; defaulted-loan reserve change: the change in the reserve on defaulted, unresolved loans; model changes and overlays: none.

## 5. Benchmarks (M-05, M-06)

| Method | Reserve 30 June 2026 | How |
|---|---|---|
| CECL engine (booked, weighted) | $268.3m | PD x LGD x EAD, scenarios, reversion, to maturity |
| Loss-rate method | $841.2m | average annual net loss rate 1999-2025 (0.134%) x balance x expected remaining life (7.4 years) |
| Vintage method | $680.6m | remaining part of the average cumulative loss curve of mature vintages 1999-2012 x each book vintage's original balance x share still on the book |

The benchmarks are through-the-cycle: both include the 2008 crisis losses at full weight, while the engine uses the current book's own credit quality and the Fed's scenarios. Differences are expected; they are a range check, not a target.

### Freddie Mac's own allowance ratio (information only, not a target)

| | Ratio | On our 30 June 2026 book |
|---|---|---|
| Our total allowance (booked) | 0.35% | $297.5m |
| Freddie Mac's published Single-Family ratio, 30 June 2026 | 0.19% | $160.8m |
| Gap | 0.16% | $136.7m |

Why ours is higher (illustrations, each on its own; they overlap and do not add up):

- **LGD conservatism (L-033, 1.64x).** Priced at the real loss per default, the total allowance would be about $181.4m (0.21%).
- **Scenario weights (ADR-005, 60/30/10).** The adverse and severely adverse paths supply $211.0m, 79% of the performing reserve (71% of the total allowance). On the baseline alone the allowance would be $124.7m (0.15%).
- **Different book, models and mortgage insurance.** Ours is a sample of loans alive at 30 June 2026 priced by an educational model; Freddie's covers its whole single-family guarantee book with its own models, scenarios and weights (its Q2 2026 benefit for credit losses reflected a change in how it generates house-price scenarios). Mortgage insurance and other credit enhancements enter our LGD only through Freddie's reported recoveries on past defaults; Freddie values them loan by loan.

Source: Freddie Mac, Second Quarter 2026 Financial Results (Q2 2026 earnings release), Single-Family allowance for credit losses as a percentage of total single-family loans: 0.19% at 30 June 2026, https://www.freddiemac.com/investors/financials/pdf/2026er-2q26_release.pdf. Freddie's ratio is a sanity reference for the same kind of loans, not a target; the bank peer comparison (FDIC call reports) is in Phase 6.

## 6. Disclosure tables (aggregates only)

Rollforward by loan group: section 3. Credit quality by origination year (FICO and current-LTV bands, balance), and gross write-offs by origination year (Q2 2026 and year to date): `docs/phase4/reserve_2026Q2.json` (`disclosures`), rendered in `docs/phase4/disclosures_2026Q2.md`.

## 7. Methods and assumptions

- **Forecast and reversion (ADR-003):** 2 years of the Fed's paths from July 2026 (the Fed's first quarter lands on the quarter after the reserve date), then 1-year straight-line reversion of unemployment, house-price growth and the mortgage rate to their long-run averages from 1990.
- **State paths (ADR-008 with B4b):** state change = beta x national change (betas 1991 to the reserve date, caps 0.25-2.0 unemployment and 0.25-3.0 house prices), rescaled so the book-balance-weighted state change equals the Fed's national change exactly (checked each run).
- **Scenario weighting (ADR-005):** losses weighted 60% baseline / 30% adverse / 10% severely adverse.
- **Lifetime horizon:** monthly for 36 months, then yearly steps to each loan's maturity at the month-36 chances and the long-run economy (loan-age effects after year 3 not re-scored; the tail's share is shown above).
- **LGD (M-02):** two-step model, severity capped at 150% for fitting (kept, Martins, 19 November 2026). **EAD (M-03):** schedule plus partial prepayment.

## 8. Limitations that apply (limitations log)

L-028 payoffs (largely the L-039 count, see the validation report), L-031 turnover drift, L-033 LGD conservative, L-034 EAD, L-035 crisis exam, L-036 household stress not used, L-037 downturn check test 7, L-038 holdout failed, L-039 check bias; conforming loans only; Freddie's underwriting and MI shape losses; recent vintages have incomplete losses.

## 9. Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| Maker | Martins Ogiekpolor | | |
| Checker (a different day) | Martins Ogiekpolor | | |
