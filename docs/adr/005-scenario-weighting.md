# ADR-005: Scenario weighting

- **Status:** draft, waiting for Martins' decision (Phase 0, November 2026)
- **Related:** ADR-003 (forecast and reversion), the Phase 5 sensitivity table, ADR-006 (overlays)

## Context

The future economy is uncertain, so many banks build the reserve from several possible futures,
each given a weight, instead of one forecast. The roadmap (v2, section 5.4) asks for a reserve built
from three futures: **baseline, adverse and severely adverse**, mixed by weights (for example
60 / 30 / 10). The weights and their reasons must be written down, and a table must show how much
the reserve moves when the weights change.

What we have:

- The Fed's 2026 supervisory scenarios give a **baseline** and a **severely adverse** path
  (unemployment rising to about 10%, house prices falling about 30%). They are designed for stress
  testing, not as probability-weighted forecasts.
- There is **no official adverse path** in the data sources listed for this project, so the middle
  scenario has to be built, and documented.

## Options

### A. Where the adverse scenario comes from

| Option | For | Against |
|---|---|---|
| A1. Halfway between baseline and severely adverse, quarter by quarter | Simple; reproducible; moves with the Fed's paths each year | Mechanical; a "half recession" may not be internally consistent |
| A2. A custom moderate recession built from the custom shocks (for example unemployment +2 points, house prices -10%) | Easy to explain; reuses the Phase 5 shocks | Our own judgment; needs its own justification |
| A3. A published moderate scenario from another source | External support | Adds a source to collect and licence-check |

### B. What gets weighted

| Option | For | Against |
|---|---|---|
| **B1. Weight the losses:** run the engine once per scenario, then take the weighted average of the three reserves | Correct when losses respond non-linearly to the economy (they do: a severe downturn costs far more than twice a mild one); each scenario's reserve is visible | Three engine runs per quarter (more Databricks time) |
| B2. Weight the inputs: average the three economic paths into one path, then run the engine once | One run | Understates losses, because averaging the economy smooths away the non-linear tail; harder to defend |

### C. How the weights are set

| Option | For | Against |
|---|---|---|
| C1. Fixed weights (for example 60 / 30 / 10), reviewed once a year | Stable; easy to audit; the reserve moves only with the economy and the book | Doesn't react when recession risk clearly rises or falls |
| C2. Judgment each quarter, with written reasons tied to evidence (for example forecasters' recession odds, unemployment trends) | Responsive; how many banks do it | More discretion; must be logged and approved so it can't be used to steer the number |
| C3. Weights from a published probability (for example a survey of forecasters' recession probability) mapped to the scenarios by a fixed rule | Evidence-based and rule-based | Needs a mapping rule; adds a data source |

Whatever is chosen: weights sum to 100%, the severely adverse weight has a floor and a cap (a
judgment to set here), and every weight change is a logged assumption change with a reason and
approval.

### D. The weight-sensitivity table (required in every case)

The reserve under each scenario alone (100% weight), under the chosen weights, and under a small
grid around them (for example severely adverse weight ±10 points), so readers can see how much the
weights matter.

## Consequences

- The engine runs three scenarios per reporting quarter; run time is recorded against the Databricks
  quota (ADR-001).
- The allowance memo shows each scenario's reserve, the weights, their reasons and the sensitivity
  table. The website's Scenario lab can expose the weights as a slider.
- Weights are an assumption, not an overlay: they live here and in the change log, not in the
  overlay log (ADR-006).

## Decision

*To be written by Martins.*

<!-- Points to settle: A (adverse path), B (weight losses or inputs), C (how weights are set, with
floor/cap for severely adverse), and the starting weights. -->
