# Phase 6 validation results

Rules and pass/fail tests committed before any test ran: `docs/phase6/test_designs.md`. Martins's written expectations, committed before the tests: `docs/phase6/backtest_expectations.md`. Each test ran once. Information for the validator: no booked figure, model status or overlay decision changes.

> **Model status, stated with every figure.** PD and prepayment: model v2 `@challenger` (logistic transition models, path simulation). **v2 failed its holdout** (July 2024 - June 2026, run once 19 November 2026; L-038): total defaults 0.81 of actual under the recorded check, 5 of 8 judged default cells outside 0.80-1.25. **L-039:** that check under-counted projected defaults; counted correctly the holdout total is 0.92 (3 cells outside), and in 2011-2019 v2 over-projects defaults by 27-37%. The reserve engine is not affected by L-039. **Overlay (Option B, Martins, 19 November 2026):** no L-038 overlay on the baseline reserve, because the LGD model's documented conservatism (L-033, 1.64x the real loss per default) more than offsets the default shortfall (0.74 in loss terms over 24 months); both errors are disclosed and the decision is reviewed each quarter. Every reserve is also shown with the +34% default uplift (Option A) as a sensitivity. Educational model on Freddie Mac conforming mortgages (sample book); not an audited bank reserve and not accounting advice.

## 1. Crisis backtest

_Pending._

## 2. Rolling backtest

_Pending._

## 3. Forbearance treatment test

_Pending._

## 4. Peer comparison

_Pending (FFIEC load)._

## 5. L-043: rate-shock breakdown

_Pending._
