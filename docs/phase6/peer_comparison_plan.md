# Phase 6 plan: peer comparison (Q9)

- **Status:** plan only (20 November 2026). Nothing built yet.
- **Question (Q9):** how does our reserve ratio compare with real lenders' ratios on residential mortgages?
- **Rule:** the comparison is a sanity range, not a target (L-001). Real bank books are riskier and more mixed;
  our book is Freddie Mac conforming loans only.

Educational model on Freddie Mac conforming mortgages; not an audited reserve and not accounting advice.

## Reference points

| Reference | What it measures | Source | How it is read |
|---|---|---|---|
| **Freddie Mac's own Single-Family allowance ratio** | Allowance for credit losses / total single-family loans; 0.19% at 30 June 2026 (ours 0.35%) | Freddie Mac quarterly earnings release and 10-Q (Q2 2026: https://www.freddiemac.com/investors/financials/pdf/2026er-2q26_release.pdf) | The closest like-for-like point: same kind of loans, the issuer's own models. Collected each quarter by hand from the public release (one number, no scraping) |
| Banks' residential mortgage reserve ratios | Allowance on 1-4 family residential loans / those loans, by bank-size group | FDIC call reports via Bank Health (read-only, aggregates) | A range across banks; their books include jumbo and non-conforming loans |

## What Phase 6 will show

- Our total allowance ratio, the baseline-only ratio and the ratio with the LGD conservatism removed (L-033), next to
  Freddie's ratio and the bank range, for each quarter we have a reserve.
- Freddie's ratio over time (quarters available from its releases), so a gap that changes direction is visible.
- The reasons for any gap, as in the Q2 2026 allowance memo: LGD conservatism (L-033), scenario weights (ADR-005),
  and differences in book, models and mortgage insurance.
