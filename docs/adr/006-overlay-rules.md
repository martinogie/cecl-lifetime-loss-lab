# ADR-006: Overlay rules (qualitative adjustments)

- **Status:** draft, waiting for Martins' decision (Phase 0, November 2026)
- **Related:** ADR-005 (scenario weighting), the limitations log and change log
  ([docs/governance/](../governance/)), the reserve bridge (Phase 4)

## Context

An overlay (a "qualitative" or "Q-factor" adjustment) is a documented human change to the modelled
reserve, for something the model can't see. Overlays are normal practice and expected by examiners,
but they are also where a reserve can be steered. The roadmap (v2) requires each overlay to be
**tied to a standard reason, given a dollar amount, capped, logged and approved**, shown separately
in the reserve bridge, and **never used to hide a model problem**.

The standard reasons come from the **Interagency Policy Statement on Allowances for Credit Losses
(2020)**. It lists the qualitative factors institutions consider when adjusting historical loss
information for current conditions and forecasts. In summary (check the exact wording against the
statement before the first overlay is booked):

| # | Qualitative factor (2020 interagency policy statement, paraphrased) | Applies to this project? |
|---|---|---|
| Q1 | Lending policies and procedures: underwriting standards, collection, write-off and recovery practices | Partly: Freddie's underwriting changes over time (for example credit-score and appraisal-waiver changes); not our own policies |
| Q2 | International, national, regional and local economic conditions | Yes: conditions not captured by the scenarios or the state unemployment and house-price inputs |
| Q3 | Nature and volume of the portfolio and terms of loans | Yes: shifts in the book's mix (for example more investor or high-LTV loans) |
| Q4 | Experience, ability and depth of lending, investment, collection and other relevant staff | No: there is no real bank staff; recorded as not applicable |
| Q5 | Volume and severity of past-due loans, nonaccrual loans and adversely classified or graded loans | Yes: for example a wave of loans leaving forbearance |
| Q6 | Quality of the institution's credit review function | No: recorded as not applicable |
| Q7 | Value of underlying collateral for collateral-dependent loans | Yes: house-price moves faster than the HPI inputs show, or local price falls |
| Q8 | Existence and effect of concentrations of credit, and changes in their level | Yes: state or metro concentrations, especially in disaster-prone areas |
| Q9 | Effect of other external factors: competition, legal and regulatory requirements | Yes: for example foreclosure moratoria or rule changes that delay losses |

## Options

### A. Whether to allow overlays at all

| Option | For | Against |
|---|---|---|
| A1. No overlays: model output only | Nothing to steer | Unrealistic; ignores known model gaps; examiners expect a framework |
| **A2. Overlays allowed only under the rules below** | Matches practice; transparent | Needs governance (log, approval, review) |
| A3. Free judgment adjustments | Flexible | Exactly what the roadmap forbids |

### B. Rules every overlay must follow (if A2)

| Rule | Options to choose from |
|---|---|
| **Reason** | Must name one factor Q1 to Q9 (Q4 and Q6 are not applicable here). An overlay for a known model weakness ("model limitation") also needs a limitations-log entry with an owner and fix date |
| **Sizing** | (i) a quantified method, for example re-running the engine with a stressed input such as house prices -5% in one state, and taking the difference; or (ii) a percentage of the modelled reserve for a segment, with written support. Option (i) is easier to defend |
| **Caps** | Per overlay and in total, as a share of the modelled reserve (for example at most 5% each and 10% in total; the numbers are a decision for this ADR). Anything above a cap needs a separate written justification and is flagged in the memo |
| **Direction** | Allow both increases and decreases, or increases only. Decreases get more scrutiny in practice |
| **Expiry** | Each overlay expires at quarter end unless renewed with fresh evidence, or lasts until a stated end date |
| **Persistence** | If the same overlay is renewed for more than two quarters, the model must be fixed instead: a limitations-log item with a fix date is opened. This is how overlays are kept from hiding a model problem |
| **Approval** | Maker / checker: Martins prepares the overlay (maker) and, separately, reviews it in writing (checker), recorded in the overlay log with dates |

### C. Where overlays appear

- An **overlay log** (date, factor, segment, amount, sizing method, evidence, cap check, maker,
  checker, expiry), in the quarterly committee pack.
- A separate line in the **reserve bridge** and the **rollforward notes**, so the modelled reserve
  and the overlay are always visible apart.
- The **controls matrix** has a control that checks every overlay against these rules each quarter.

## Consequences

- The modelled reserve is always reported first; overlays are added on top and never mixed into
  model outputs.
- Factors Q4 and Q6 are recorded as not applicable, with the reason (no real bank staff or credit
  review function), so a reviewer sees they were considered.
- A persistent overlay automatically becomes a model-fix item, which keeps the limitations log
  honest.

## Decision

*To be written by Martins.*

<!-- Points to settle: A, then each rule in B (especially the cap numbers, direction and expiry),
and confirm the factor list against the 2020 statement's exact wording. -->
