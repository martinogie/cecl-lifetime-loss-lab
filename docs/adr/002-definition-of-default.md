# ADR-002: Definition of default

- **Status:** draft, waiting for Martins' decision (Phase 0, November 2026)
- **Related:** data contracts ([freddie_perf_r48.yml](../../ingestion/contracts/freddie_perf_r48.yml)),
  ADR-003 (forecast and reversion), the Phase 6 forbearance test

## Context

"Default" is the event the PD model predicts, the event LGD is measured from, and the moment EAD
is taken. It must mean exactly the same thing in the monthly panel, the PD, LGD and EAD models, the
backtests and the disclosures. CECL itself does not define default; banks choose one and document it.
The working definition in CLAUDE.md is **90+ days delinquent or a loss event**.

**What Freddie's monthly performance file gives us** (General User Guide, Release 48):

| Field | Values | Use for default |
|---|---|---|
| Current loan delinquency status | `00` current, `01` 30–59 days, `02` 60–89, `03` 90–119 … capped at `99`; `RA` REO acquisition; `XX` not available | 90+ days = status `03` or more; `RA` = the lender took the house |
| Zero balance code (why the loan ended) | `01` prepaid or matured; `02` third-party sale; `03` short sale or charge-off; `09` REO disposition; `15` whole loan sale; `16` reperforming loan securitization; `96` defect found before a credit event | `02`, `03`, `09` are loss events; `01` is prepayment; `96` removes the loan for a seller defect |
| Delinquency due to disaster | `Y` or empty (from January 2014) | marks disaster-related hardship, often in forbearance |
| Borrower assistance plan | `F` forbearance, `R` repayment, `T` trial period, or empty (from January 2014) | marks a payment pause or workout |
| Modification flag | `Y` modified this month, `P` modified earlier | loan terms changed |
| Payment deferral flag | `C` this month, `P` earlier | missed payments moved to the end of the loan, usually when forbearance ends |
| Actual loss fields | net sale proceeds, recoveries, expenses, actual loss | populated at disposition; Freddie computes actual loss for codes `02`, `03`, `09`, `15` |

**Why forbearance matters.** Freddie's delinquency status counts from the last paid installment, so a
borrower in an agreed payment pause looks 30, 60, 90+ days behind even though the pause was
permitted. In 2020 to 2021 COVID forbearance (CARES Act, later extended for GSE loans) put a large
share of loans into this state at once. Most then left forbearance through a payment deferral or
modification and never lost money. Counting every 90+ day forbearance month as a default would make
2020 look like a severe default wave with almost no losses. That would distort PD (too high),
LGD (too low, because most of those "defaults" cured at no loss) and the backtests. Earlier
disasters (for example the 2017 hurricanes) show the same pattern on a smaller scale, so the rule
should cover all disaster forbearance, not just COVID.

## Options

### A. What triggers a default

| Option | For | Against |
|---|---|---|
| **A1. 90+ days delinquent (status `03`+), or `RA`, or a loss-event zero balance code (`02`, `03`, `09`)** | Matches the working definition and common bank and Basel practice (90 days); catches defaults early; enough events to model | Some 90+ day loans cure with no loss, so LGD needs a "no loss" step (planned anyway) |
| A2. 180+ days delinquent or a loss event | Close to when US banks charge mortgages down to collateral value; fewer cures | Later, fewer events; weaker PD signal; out of step with the working definition |
| A3. 60+ days delinquent or a loss event | Earliest warning | Many cures; inflates PD and deflates LGD |

How to treat the other zero balance codes under any option:

- `15` (whole loan sale): often a sale of non-performing loans with a recorded loss. It could count
  as a loss event, or as a default only if the loan was already 90+ days late.
- `16` (reperforming loan securitization): the loan was performing again when sold, so not a
  default by itself. Censor it, unless an earlier month already triggered default.
- `96` (defect): censor at that month; it says nothing about borrower credit.

### B. One default per loan, or cures and re-defaults

| Option | For | Against |
|---|---|---|
| **B1. First default only (absorbing):** the loan leaves the PD panel at its first default; what happens after (cure, modification, loss) feeds LGD | Standard for lifetime-PD hazard models; avoids double counting; simple survival maths | Cures are not modelled as such; they appear as zero-loss outcomes in LGD |
| B2. Allow cure and re-default | Closer to how loans really move | Multi-state model; more complex; harder to reconcile to losses |

### C. Modifications

| Option | For | Against |
|---|---|---|
| C1. A modification is not a default by itself; default still needs A's trigger | Simple; uses observable delinquency | A loan modified just before reaching 90 days escapes the definition |
| C2. A modification while 60+ days delinquent counts as default (a distressed restructuring) | Catches defaults avoided only through a workout; closer to accounting views of credit-impaired loans | More rules; needs the delinquency history at the modification date |

### D. Disaster and COVID forbearance

| Option | For | Against |
|---|---|---|
| D1. No special treatment: 90+ days counts as default even in forbearance | Simplest; one rule everywhere | 2020–2021 becomes a false default wave; PD, LGD and backtests distorted |
| **D2. Stop the clock:** months flagged as disaster delinquency (`Y`) or forbearance (`F`) don't count towards the 90 days. Default is triggered only if the loan is 90+ days late *outside* flagged months (for example still 90+ days late a set number of months after the flags clear, or with no deferral or modification), or by a loss event | Separates real defaults from permitted pauses; uses Freddie's own flags; applies to every disaster, not just COVID | Needs a rule for how long after the flags clear; flags exist only from January 2014, so earlier disasters can't be treated the same way |
| D3. Drop 2020 to 2021 from model training | Avoids the problem in training | Throws away recent data; the reserve still has to handle forbearance today; the backtest can't test it |
| D4. Count as default but add a forbearance indicator to the model | The model learns the difference | The default count and LGD are still distorted; harder to explain |

The Phase 6 forbearance test compares the chosen option with D1 on 2020 to 2021, as the roadmap
requires.

### E. Censoring (the loan leaves the data without a default)

Prepayment (`01`) is the competing outcome; data end, `96` and `16` (unless already defaulted) are
censored. Months with status `XX` (not available) need a rule: carry the previous status forward,
or treat the month as missing.

## Consequences

- The chosen definition becomes code in one place (the panel builder in `cecl.panel`, Phase 1) and
  every other component reads it from there. A change after Phase 2 means re-running PD, LGD, EAD
  and the backtests, recorded in the change log.
- The default counts by vintage are compared with Freddie's published statistics (Phase 1). A
  different definition from Freddie's will explain part of any gap.
- The forbearance rule is a modelling assumption that goes in the limitations log, with the Phase 6
  test as its evidence.

## Decision

*To be written by Martins.*

<!-- Points to settle: A (trigger, plus codes 15/16/96), B (absorbing or not), C (modifications),
D (forbearance rule, and how long after the flags clear), E (XX months). -->
