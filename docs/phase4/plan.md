# Phase 4 plan: the CECL engine

**Status (20 November 2026): complete.** Decisions taken 19 November 2026: overlay Option B (L-038; L-033 netting decision), ADR-008 B4b, LGD cap 150% kept, horizon to maturity (monthly 3 years, then yearly). Completed 20 November 2026: forecast and reversion as settings, the overlay framework (`cecl/overlays.py`, `docs/governance/overlay_log.json`), and the 31 December 2025 reserve (Fed 2025 scenarios, point in time) giving rollforwards and bridges for Q1 and Q2 2026. Results: `docs/allowance_memos/2026Q2_allowance_memo.md`, `docs/phase4/reserve_2026Q2.json`, `docs/phase4/disclosures_2026Q2.md`. Open: the memo's checker sign-off (Martins, a different day). The text below is the original proposal.

Proposed 19 November 2026, after Phase 3. Nothing here is built yet. Educational model on Freddie Mac
conforming mortgages; not an audited reserve and not accounting advice.

## What the engine computes

For every loan alive on the reserve date (**30 June 2026**) and every future month k:

    expected loss_k = P(default in month k)  x  LGD_k  x  EAD_k

- **P(default in month k):** the multi-state projection (`cecl/models/multistate.py`) with the
  @challenger transition models, fed the scenario's economic path instead of the actual one.
- **LGD_k:** the Phase 3 two-step model, fed the loan's projected LTV (EAD_k over the original value moved
  by the scenario's house-price path) and the path's 12-month house-price change.
- **EAD_k:** the scheduled balance less partial prepayment (Phase 3).

Summed over months: each loan's lifetime expected loss; summed over loans: the modelled reserve. A
tie-out checks that the book reserve equals the sum of loan reserves (CLAUDE.md engineering standard).

## Economic paths (ADR-003, ADR-005)

- 2-year reasonable-and-supportable forecast from the Fed's 2026 paths (baseline, adverse halfway path,
  severely adverse; national paths, quarterly to monthly), from July 2026;
- then 1-year straight-line reversion to the long-run averages from 1990 (already in silver);
- state paths: **ADR-008 is still pending**. Martins leans to rescaling the state paths to the national
  path (memory note "B4b"): to be confirmed before it is built; until then a uniform national shift,
  labelled as provisional.
- Weighted reserve: 60% baseline / 30% adverse / 10% severely adverse (ADR-005), plus a weight-sensitivity
  table.

## Overlays (ADR-006), including L-032

- Every overlay names a standard factor (Q1-Q9), is sized by re-running the engine, capped (5% of the
  modelled reserve each, 10% in total), expires each quarter, and is approved maker/checker.
- **L-032 (the out-of-time under-prediction), Martins's decision (3):** proposed as a model-limitation
  overlay under factor Q2 (changes in economic conditions: rate lock-in). Sizing evidence from the
  diagnosis: correcting the rate-lock roll lifted projected defaults by about 58% on the test years.
  **Decision needed:** a correction of that size is far above the 5% / 10% caps in ADR-006. Options:
  (a) keep the caps, book the capped amount and disclose the uncapped size next to it; (b) Martins
  approves an exception for a documented model limitation, recorded as a deviation; (c) treat it as a
  model adjustment outside the overlay framework (a new model version, validated on a new period).
- L-031 (turnover drift) and L-033 (LGD conservative) are reported, not netted against L-032.

## Benchmarks, rollforward, bridge, disclosures, memo

- Loss-rate method and vintage method (M-05, M-06), with the differences explained.
- Two quarter-ends (31 March and 30 June 2026), each rebuilt from its own data snapshot: rollforward
  (start + provision - write-offs + recoveries = end, to the cent) and the reserve bridge (new loans,
  payoffs, credit quality, economic forecast, model changes, overlays).
- Disclosure tables (10-Q style, aggregates only): rollforward by loan group; credit quality (FICO and
  LTV bands) by origination year; gross write-offs by origination year.
- First allowance memo (`docs/allowance_memos/`), stating the out-of-time result (the pre-registered
  statement) next to every reserve figure.

## Compute

Local first (the sample book on 30 June 2026 is about 260,000 loans; one 10-year projection per
scenario is about 20-30 minutes on the PC), then a Databricks job for the three scenarios and the
sensitivity runs (estimate 30-45 minutes a full run), never Monday before 13:00 or 02:30-04:00.

## Decisions needed from Martins before building

1. The L-032 overlay route (a), (b) or (c) above.
2. ADR-008 state mapping (B4b): confirm rescaling to the national path.
3. The LGD severity cap at 150% (Phase 3).
4. The lifetime horizon: run to each loan's maturity, or cap at, for example, 10 years with the tail
   at the reverted long-run rates (proposal: to maturity, in yearly steps after year 3, for speed).
