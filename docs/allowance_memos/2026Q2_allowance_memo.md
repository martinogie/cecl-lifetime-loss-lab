# Allowance memo: Q2 2026 (reserve date 30 June 2026)

- **Prepared by:** Martins Ogiekpolor (maker). **Checker review:** _pending (a different day, ADR-006)._
- **Book:** Freddie Mac Single-Family sample loans alive at the reserve date. **Scenarios:** Federal Reserve baseline and severely adverse, plus the halfway adverse path (ADR-005), from the quarter after each reserve date. Each reserve uses only the Fed scenarios published by its date: 2025 scenarios for 31 December 2025, 2026 scenarios for 31 March and 30 June 2026 (point in time, Martins, 20 November 2026).
- **Forecast and reversion (ADR-003):** 24 months, then 12 months straight-line reversion (settings of the projection job and the pricing; checked to match).
- **Source:** `docs/phase4/reserve_2026Q2.json` (`scripts/build_reserve.py`); loan-level data never leaves the PC or Databricks.

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

## 1. The reserve

| | 31 December 2025 | 31 March 2026 | 30 June 2026 |
|---|---|---|---|
| Fed scenarios used (published by the reserve date) | 2025 | 2026 | 2026 |
| Loans | 340,358 | 341,291 | 350,495 |
| Balance | $80,546.0m | $81,024.4m | $84,623.6m |
| Performing-loan reserve (engine, weighted 60/30/10) | $302.0m | $262.7m | $268.3m |
| Performing reserve ratio (reserve / balance) | 0.375% | 0.324% | 0.317% |
| Overlays booked (ADR-006 register) | $0.0m | $0.0m | $0.0m |
| Defaulted, unresolved loans (90+ days late or REO): loans / balance | 1,857 / $440.6m | 1,953 / $467.3m | 2,059 / $507.9m |
| Defaulted-loan reserve (balance x Phase 3 LGD) | $26.2m | $27.4m | $29.2m |
| **Total allowance (booked)** | **$328.2m** | **$290.1m** | **$297.5m** |
| Total allowance ratio | 0.405% | 0.356% | 0.349% |
| Sensitivity: total with the +34% default uplift on the performing reserve (Option A) | $431.9m | $380.3m | $389.7m |
| Tie-out: book reserve = sum of loan reserves (every scenario) | pass | pass | pass |

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

### By scenario, 31 December 2025 (Fed 2025 scenarios)

| Scenario | Reserve | Of which: forecast (yr 1-2) / reversion (yr 3) / tail | Balance-weighted lifetime PD | With +34% uplift |
|---|---|---|---|---|
| Baseline | $78.9m | $57.7m / $16.3m / $4.9m | 2.21% | $106.0m |
| Adverse (halfway path) | $448.8m | $408.7m / $33.4m / $6.7m | 3.82% | $602.9m |
| Severely adverse | $1,200.1m | $1,105.1m / $78.4m / $16.7m | 5.79% | $1,612.3m |
| **Weighted 60/30/10 (booked)** | **$302.0m** | | | $405.7m |

### PD x LGD x EAD by scenario, 30 June 2026 (performing loans)

Balance-weighted averages over the 350,495 loans. Lifetime PD: each loan's chance of default over its whole remaining life. LGD: loss per $ defaulted. EAD: balance at default / current balance. Each loan's figures already include the timing of defaults (36 monthly steps, then yearly to maturity), so for every loan PD x LGD x EAD x balance equals its reserve exactly.

| Scenario | Lifetime PD | LGD | EAD ratio | PD x LGD x EAD x balance | Reserve | Gap |
|---|---|---|---|---|---|---|
| Baseline | 2.290% | 3.724% | 0.7949 | $57.4m | $95.5m | $38.1m |
| Adverse (halfway path) | 3.616% | 7.694% | 0.7735 | $182.1m | $383.9m | $201.8m |
| Severely adverse | 5.441% | 12.152% | 0.7617 | $426.2m | $958.4m | $532.3m |
| **Weighted 60/30/10** | 3.003% | 9.124% | 0.8159 | $189.2m | $268.3m | $79.2m |

The simple averages multiply to less than the reserve because the risk sits in the same loans: loans more likely to default also default earlier (more balance left) and have higher LGD (higher LTV). Weighting EAD by each loan's expected defaults, and LGD by each loan's expected exposure, closes the gap exactly:

| Scenario | Lifetime PD (by balance) | EAD ratio (by expected defaults) | LGD (by expected exposure) | Product x balance | Reserve | Gap closed by EAD weighting / by LGD weighting |
|---|---|---|---|---|---|---|
| Baseline | 2.290% | 0.9168 | 5.376% | $95.5m | $95.5m | $8.8m / $29.3m |
| Adverse (halfway path) | 3.616% | 0.9311 | 13.475% | $383.9m | $383.9m | $37.1m / $164.7m |
| Severely adverse | 5.441% | 0.9315 | 22.349% | $958.4m | $958.4m | $95.0m / $437.3m |
| **Weighted 60/30/10** | 3.003% | 0.9246 | 11.420% | $268.3m | $268.3m | $25.2m / $54.0m |

Timing and the tail sit inside each loan's figures, not in the gap. Of the baseline lifetime PD 2.290%, 1.790% falls in the first 36 months and 0.500% after (the tail supplies 8.5% of the baseline reserve; 3.1% under severely adverse). Losses are not discounted. Weighted column: each loan's PD, expected exposure and loss are the 60/30/10 sums across scenarios. Source: `docs/phase4/reserve_components_2026Q2.json` (`scripts/decompose_reserve.py`, reading the priced loan files only; nothing re-run). Every scenario ties to the reserve file.

### Severely adverse, with and without the uplift (Martins's requirement)

The LGD conservatism (L-033) was measured on resolved 2017-2021 defaults, good years; it may not hold in a downturn, so the severely adverse reserve is shown both ways:

| Date | Severely adverse, no uplift | Severely adverse, +34% uplift |
|---|---|---|
| 31 December 2025 | $1,200.1m | $1,612.3m |
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
- Overlays booked: **none** at any of the three dates (register below).

### Overlay register (ADR-006; `cecl/overlays.py`, log `docs/governance/overlay_log.json`)

Every proposed overlay is checked each run: a standard factor (Q1-Q9; Q4 and Q6 not applicable), the numbers method (or a written justification for a percentage), caps of 5% each and 10% in total of the modelled reserve (or a written exception flagged here), the numbers method and the checker's sign-off for a decrease, expiry at quarter-end, at most two renewals before the model must be fixed, and maker/checker on different days. Only approved overlays that pass every rule are booked.

| Reserve date | Modelled reserve | Overlays proposed | Booked | Booked total | Share of modelled |
|---|---|---|---|---|---|
| 31 December 2025 | $302.0m | 0 | 0 | $0.0m | 0.0% |
| 31 March 2026 | $262.7m | 0 | 0 | $0.0m | 0.0% |
| 30 June 2026 | $268.3m | 0 | 0 | $0.0m | 0.0% |

Considered and not proposed:

- **31 December 2025, A-2025Q4-1 (Q5):** L-038, as for 30 June 2026 (built later, 20 November 2026, for the Q1 2026 rollforward). Decision: No overlay (Option B), same decision and disclosures as A-2026Q2-1. (Martins Ogiekpolor, 2026-11-19; review each quarter).
- **31 March 2026, A-2026Q1-1 (Q5):** L-038, as for 30 June 2026 (the first reserve build covers both dates). Decision: No overlay (Option B), same decision and disclosures as A-2026Q2-1. (Martins Ogiekpolor, 2026-11-19; review each quarter).
- **30 June 2026, A-2026Q2-1 (Q5):** L-038: model v2 under-projected holdout defaults (0.74 of actual in expected-loss terms over July 2024 - June 2026; +34% would close it). Decision: No overlay (Option B): the L-038 default shortfall is assessed at the expected-loss level; the LGD model's documented conservatism (L-033, 1.64x) more than offsets it. Both errors disclosed with every reserve figure; +34% shown as a sensitivity. Baseline only; reviewed each quarter. (Martins Ogiekpolor, 2026-11-19; review each quarter).

## 3. Rollforward, Q1 and Q2 2026 (to the cent)

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

Start + provision - gross write-offs + recoveries = end, on the total allowance (performing reserve, booked overlays and the defaulted-loan reserve); provision is the balancing figure. Write-offs are Freddie's realised losses at disposition (a gain counts as zero); recoveries are MI and other recoveries.

### Q1 2026

| Loan group (origination year) | 31 December 2025 | Provision | Gross write-offs | Recoveries | 31 March 2026 |
|---|---|---|---|---|---|
| 2018 and older | 11918246.92 | -1234630.90 | -538503.42 | 39468.95 | 10184581.55 |
| 2019-2021 | 24511201.86 | -4022244.33 | -656368.76 | 44027.32 | 19876616.09 |
| 2022-2024 | 212062549.50 | -37164014.10 | -2245454.95 | 1043599.39 | 173696679.84 |
| 2025-2026 | 79707081.40 | 6622665.08 | 0 | 0 | 86329746.48 |
| **Total** | **328199079.68** | **-35798224.25** | **-3440327.13** | **1127095.66** | **290087623.96** |

Reconciles to the cent: **yes**. Loss dispositions in the quarter: 39, of which 1 **estimated** (Freddie reports losses about 3 months after the sale; write-offs not yet reported are estimated (balance removed x Phase 3 LGD) and trued up when reported).

### Q2 2026

| Loan group (origination year) | 31 March 2026 | Provision | Gross write-offs | Recoveries | 30 June 2026 |
|---|---|---|---|---|---|
| 2018 and older | 10184581.55 | -403057.53 | -57907.84 | 0.00 | 9723616.18 |
| 2019-2021 | 19876616.09 | -1604611.24 | -58305.02 | 0.00 | 18213699.83 |
| 2022-2024 | 173696679.84 | -10223269.13 | -749362.14 | 0.00 | 162724048.57 |
| 2025-2026 | 86329746.48 | 20506392.98 | 0 | 0 | 106836139.46 |
| **Total** | **290087623.96** | **8275455.08** | **-865575.00** | **0.00** | **297497504.04** |

Reconciles to the cent: **yes**. Loss dispositions in the quarter: 57, of which 57 **estimated** (Freddie reports losses about 3 months after the sale; write-offs not yet reported are estimated (balance removed x Phase 3 LGD) and trued up when reported).

## 4. Reserve bridges (weighted, total allowance)

Exits: the start reserve of loans that left the book; new loans: the end reserve of loans not in the start book; credit quality and ageing: continuing loans under the start economy, end vs start; economic forecast: the end economy vs the start economy for the end book; overlays: change in booked overlays; defaulted-loan reserve change: the change in the reserve on defaulted, unresolved loans; model changes: none (same models).

### 31 December 2025 to 31 March 2026

| Step | Amount |
|---|---|
| Start | $328.2m |
| Exits | $-20.0m |
| New loans | $17.5m |
| Credit quality and ageing | $11.7m |
| Economic forecast | $-48.4m |
| Model changes | $0.0m |
| Overlays | $0.0m |
| Defaulted loan reserve change | $1.1m |
| End | $290.1m |

Bridge reconciles: **yes**; start and end equal the booked total allowance: **yes**. The economic forecast step includes the move from the Fed's 2025 scenarios to its 2026 scenarios (published in between).

### 31 March 2026 to 30 June 2026

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

Bridge reconciles: **yes**; start and end equal the booked total allowance: **yes**.

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

L-028 payoffs (largely the L-039 count, see the validation report), L-031 turnover drift, L-033 LGD conservative, L-034 EAD, L-035 crisis exam, L-036 household stress not used, L-037 downturn check test 7, L-038 holdout failed, L-039 check bias, L-040 tail, L-041 allowance components outside the engine, L-042 December 2025 reserve built after the fact; conforming loans only; Freddie's underwriting and MI shape losses; recent vintages have incomplete losses.

## 9. Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| Maker | Martins Ogiekpolor | | |
| Checker (a different day) | Martins Ogiekpolor | | |
