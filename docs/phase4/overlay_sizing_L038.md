# Overlay sizing for L-038 (proposal for Martins's review; nothing booked)

- **Status:** proposed, 19 November 2026. Awaiting Martins's decision (ADR-006: maker/checker, cap
  exception needs written justification). Not booked; no reserve figure uses it yet.
- **Model:** M-01 v2 `@challenger` (holdout failed, L-038), Phase 3 LGD (M-02) and EAD (M-03).
- **Evidence:** `docs/phase4/overlay_sizing_L038.json` (aggregates only), from `scripts/size_overlay_v2.py`
  on v2's monthly default chances for the 1 July 2024 book (Databricks job `overlay_inputs_v2`; it reproduces
  the recorded holdout total exactly, 3,081.78, before saving anything).

Educational model on Freddie Mac conforming mortgages; not an audited reserve and not accounting advice.

## Method

Over the 24 holdout months (July 2024 - June 2026), the economy as it actually happened:
- **projected expected loss (EL)**: the sum over loans and months of v2's default chance x the EAD (payment
  schedule plus partial prepayment) x the Phase 3 LGD for that month's LTV and house-price change;
- **actual defaults at the model's LGD**: the same loans' 3,814 real defaults, each at its balance at
  default x the Phase 3 LGD for its inputs at default. Comparing these two isolates the **PD shortfall in
  loss terms**;
- **the LGD offset (L-033)** shown separately: the Phase 3 LGD predicts 1.64 times the real loss per default
  on resolved 2017-2021 defaults (0.072 vs 0.044). The holdout's own resolved defaults are not used: only
  1,830 of 3,814 are resolved, and they are mostly quick cures and payoffs (LGD 0.004), a biased sample.

**Measure (new finding, L-039):** projected months are counted up to censoring only, where a loan's outcome
is unknown. The holdout check also stopped counting at an actual payoff or default. The projected chances
already include each loan's own chance of leaving, so stopping at the actual exit counts survival twice
and biases the projection low. Both measures are shown; the sizing uses the unbiased one.

## Results

| | Unbiased measure (used) | Recorded holdout rule |
|---|---|---|
| Projected defaults vs actual 3,814 | 3,517 (0.92) | 3,082 (0.81) |
| Projected EL | $33.8m | $29.0m |
| Actual defaults at the model's LGD | $45.4m | $45.4m |
| **PD gap in loss terms (projected / actual)** | **0.74** | 0.64 |
| Uplift needed to close it | **+34%** | +56% |

Why the loss gap (0.74) is wider than the count gap (0.92): 0.74 = 0.92 (defaults) x 0.95 (average balance
at default: projected $228k vs actual $239k) x 0.85 (LGD: projected defaults sit on lower-LTV loans, 4.2%,
than the real defaults priced at the same model, 5.0%). The real defaults came from bigger, higher-LTV loans
than v2 expected, which matches the recent-vintage shortfall.

| Where the gap is (projected / actual, loss terms) | Ratio |
|---|---|
| Vintages 2022-2024 (82% of the actual loss) | 0.73 |
| Vintages 2019-2021 | 0.87 |
| Vintages 2018 and older | 0.79 |
| Loans current at the start / 30 days / 60 days | 0.72 / 0.80 / 0.85 |
| Defaults in 2024 (Jul-Dec) / 2025 / 2026 (Jan-Jun) | 0.99 / 0.71 / 0.60 |

**The LGD offset, shown explicitly.** Priced at the real loss per default (model LGD / 1.64), the actual
defaults would have lost about $27.7m. Projected EL ($33.8m) is **1.22 times** that, so in net terms the
modelled EL is already above the estimated real loss. The PD shortfall (-26%) is more than offset by the
LGD conservatism (+64%).

**ADR-006 cap check:** the gross PD uplift (+34%) is far above the 5% cap per overlay and the 10% total.
The net figure (-18%) would be a decrease, which ADR-006 allows only with the numbers method and the
checker's written sign-off; L-033 also says the LGD conservatism is reported on its own and never netted
against the PD shortfall.

Limits of this sizing: 24 months, not a lifetime (the shortfall grows in the second year, so a lifetime gap
may be larger); one period (2024-2026); actual losses are estimated through the L-033 ratio because most
holdout defaults are unresolved.

## Options for Martins's decision

| Option | What is booked | ADR-006 | Effect |
|---|---|---|---|
| **A. Gross PD overlay, cap exception** | +34% of modelled EL, factor **Q5** (past-due loans: fewer cures and loans lingering at 60 days, per the L-038 diagnosis), concentrated on vintages 2022-2024 and loans current at the start; the LGD conservatism disclosed next to it, not netted | Exception above both caps, with written justification, flagged in the memo; expires at quarter-end; under the persistence rule, renewed more than twice means the model must be fixed | Consistent with L-033 (no netting). Total reserve about 1.6 times the estimated real loss: conservative |
| **B. Net: no overlay** | Nothing booked. The PD shortfall and the LGD conservatism are both disclosed, with the net position (modelled EL 1.22 times estimated real loss) | Within caps (no overlay). Needs Martins to change L-033's "never netted" mitigation, in writing | Closest to the estimated real loss; relies on one LGD check period (2017-2021) |
| **C. Capped overlay** | +10% (the total cap) under Q5, the remaining gap disclosed | Within caps | Partial cover; the memo states that the gap exceeds the cap |

**Recommendation (for discussion, Martins decides):** **A**, because it follows the rules as written (L-033:
no netting; ADR-006: an exception is allowed with justification). The memo should state plainly that the
LGD is conservative by about 1.6 times, so the reserve is likely above the real loss. B is the economically
closer number, but it needs an explicit decision to net two model errors against each other, which
validators challenge.

Every reserve output will state: the v2 holdout result (failed, L-038), the measurement note (L-039), the
overlay chosen (or none) and its size, and that the model is educational, not an audited reserve.
