# Phase 1, step 7: economic data

- **Status:** built, 15 November 2026; all decisions below taken by Martins the same day (M1 and
  L1 as built; state mapping in ADR-008; the Fed paths shifted to the reserve date, amending
  ADR-003; the October 2025 gap fill confirmed).
- **What:** the public economic series the models need, from 1990 to the latest month, each
  checked by a data contract, saved raw, then bronze (as published), then silver (monthly, ready
  for the panel and the engine). Run by the Airflow DAG `cecl_macro_data` (dev on the PC, prod on
  the VM later), Wednesdays 08:00 Chicago, never in a blocked window.
- **Why:** current LTV needs state house prices; the refinance incentive needs mortgage rates; the
  PD model needs state unemployment; ADR-003 needs long-run averages from 1990; and the Fed's
  2026 paths must line up with the 30 June 2026 reserve date.

## Sources

| Source | Series | Frequency | Key? |
|---|---|---|---|
| FRED (St. Louis Fed) | National unemployment `UNRATE`; 30-year mortgage rate `MORTGAGE30US` (Freddie Mac's weekly survey); Treasury yields `GS10`, `GS5`; 3-month bill `TB3MS` | monthly (mortgage rate weekly) | `FRED_API_KEY` |
| FRED, republishing BLS | State unemployment `<ST>UR`, 50 states and DC, seasonally adjusted (Martins chose FRED over BLS directly, 15 Nov 2026) | monthly | same key |
| FHFA | All-transactions house price index: 50 states and DC, and the US | quarterly | none (public file) |
| Federal Reserve | 2026 supervisory scenarios, domestic: baseline and severely adverse, 13 quarters (2026 Q1 to 2029 Q1); and the Fed's history file, whose last quarter (2025 Q4) is the scenarios' starting point | quarterly | none (public file) |

The Treasury series are the 5-year and 10-year yields and the 3-month bill, because those are the
maturities in the Fed's scenarios (the earlier plan said 2-year; the Fed's paths have no 2-year).

## Decision M: filling the months of FHFA's quarterly house prices (approved as built, 15 Nov 2026)

FHFA's state indexes are quarterly; the loan panel is monthly. Each quarter's value is an average
over the quarter, so it belongs to the **middle** month (February, May, August, November).

| Option | How | For | Against |
|---|---|---|---|
| **M1. Straight line between quarter midpoints, in log terms** *(recommended, built)* | Place each quarter at its middle month; fill the months between with a constant monthly growth rate | Smooth; correct timing; the three months average back to (almost exactly) the quarter's value; one line of maths to explain | Smooths any within-quarter turn |
| M2. Step: every month of a quarter gets the quarter's value | Copy | Simplest; no estimation | Jumps every quarter; a loan's LTV moves in steps |
| M3. Straight line between quarter **ends** (March, June...) | Treat the quarter's value as its last month's | June 2026 is known exactly, no extrapolation | Dates every value 1.5 months late; LTV lags the market |
| M4. Shape the months with FHFA's monthly purchase-only index (census division) | Interpolate so the months follow the monthly index | Most accurate within the quarter | Division level, not state; purchase-only, not all-transactions; more moving parts |

**Latest quarter.** With M1 the last known point is the middle of the last quarter: FHFA's
2026 Q2 sits at May 2026, so **June 2026 (the reserve month) needs one month beyond the data**:

| Option | How | For | Against |
|---|---|---|---|
| **L1. Carry the last quarter's monthly growth forward** *(recommended, built)* | June = May × the monthly growth from February to May; at most 2 months; flagged `provisional` | No systematic lag in a rising or falling market; replaced as soon as FHFA publishes the next quarter (late November for Q3) | An extrapolation, however short |
| L2. Hold flat at the last midpoint | June = May | No extrapolation | Biased low when prices rise (and high when they fall) |
| L3. Use FHFA's monthly purchase-only index for the last month | Growth from the monthly index | Real data | Different index, division level |

Whichever is chosen: extrapolated months carry `provisional = true` in silver, and more than two
extrapolated months fails the freshness check. FHFA revises past quarters in each release, so every
download is kept (raw folder by date) and the quarter-end snapshot (step 8) records which one was
used.

The same rule turns the state **scenario** paths from quarterly to monthly (ADR-008 D), so history
and forecast join without a seam.

## Decision S: state scenario paths (decided in ADR-008, 15 Nov 2026)

The Fed's scenarios are national only. [ADR-008](../adr/008-state-scenario-mapping.md) records the
options, the evidence and Martins's Decision: each state's path is the Fed's national change scaled
by the state's historical sensitivity to the national series (estimated from 1991 onward), with
limits on extreme values, recorded with each run. Step 7 delivers the state history and the
national Fed paths; the mapping itself is applied in `cecl.scenarios` (Phases 4 and 5).

## Finding G: October 2025 unemployment is missing (fill confirmed by Martins, 15 Nov 2026)

BLS published no October 2025 household-survey figures (the 2025 federal government shutdown), so
FRED has no value for national or any state unemployment in October 2025. The contract lists that
month as a **known gap**: any other missing month still stops the load. In silver the month is
filled on a straight line between September and November 2025 and marked `gap_filled` (recorded as
L-021). It matters little for the models (one month, between two known values), but it must never
be read as a real observation.

## Finding T: ADR-003's timing rule halved the severe shock (decided: T2, 15 Nov 2026)

ADR-003 says the Fed's path applies from July 2026, as quarter-by-quarter changes from the Fed's
2026 Q2 value, on top of our real June 2026 values. Built exactly that way first, the numbers
showed a problem: **the Fed's severe recession starts in 2026 Q1**,
from its end-2025 starting point. By 2026 Q2, the Fed's path already has unemployment at 7.2%
(from 4.5%) and house prices 12% lower. Our real data for the first half of 2026 show no such
recession, and measuring changes only from Q2 drops that part of the shock:

| Severely adverse, at its worst | Fed's own path | Ours, ADR-003 as written (T1) | Ours, shifted (T2) |
|---|---|---|---|
| Unemployment peak | 10.0% (2027 Q3) | **7.0%** (4.2% real June + 2.8 points) | **9.7%** (4.2% + 5.5 points) |
| House prices, peak to trough | −29.7% | **−19.7%** | **−29.7%** |

| Option | How | For | Against |
|---|---|---|---|
| T1. As ADR-003 was first written | Changes from the Fed's 2026 Q2 value apply from July 2026 | Calendar-faithful: each Fed quarter lands on the same calendar quarter | The severe scenario loses about half its designed shock; the reserve and the stress test understate the Fed's severity |
| **T2. Shift the whole path to the reserve date** *(chosen)* | The Fed's changes measured from its own starting point (end 2025) apply from July 2026: Fed quarter 1 becomes our 2026 Q3, and so on | Keeps the full designed shock; common practice when the reserve date is later than a scenario's starting point; 13 quarters still cover the 2-year forecast plus 1-year reversion | Fed quarters no longer match calendar quarters (documented in each memo) |

**Decision (Martins, 15 November 2026): T2**, recorded as an amendment under ADR-003's Decision.
The Fed's own starting point comes from its published history file (2025 Q4: unemployment 4.5%,
its house price index 323.4), downloaded and contract-checked with the scenario files. After the
change, from the dev run:

| Worst point (our dates) | Baseline | Adverse (halfway) | Severely adverse |
|---|---|---|---|
| Unemployment peak | 4.3% (2026 Q3) | 6.85% (2027 Q4) | **9.7% (2028 Q1)** |
| House prices vs June 2026, at the lowest | +0.4% (2026 Q3) | −14.2% (2028 Q2) | **−29.7% (2028 Q2)** |

The first-written timing stays in code (`timing="calendar"`) only for the Phase 5 sensitivity
table.

## How the pieces fit

| Layer | Where (local and dev Airflow, until step 9) | What |
|---|---|---|
| raw | `data/raw/macro/<source>/<download date>/` | the files exactly as downloaded (FRED as JSON per series) |
| bronze | `data/local/bronze/macro_fred/`, `macro_fhfa/`, `macro_fed/` | one Parquet table per source, as published, with download time and source file |
| silver | `data/local/silver/macro/` | `national_monthly`, `state_monthly`, `long_run_averages`, `fed_paths` |

- **Monthly rules:** weekly mortgage rates become the average of the weeks in each month (a month
  is used only once it has ended); FRED's Treasury and bill series are already monthly averages;
  FHFA follows rule M1/L1; the unemployment gap follows finding G.
- **Long-run averages (ADR-003 D2):** January 1990 to June 2026, nationally and per state.
  Unemployment and rates revert to their average **level**; house prices revert to their average
  **growth** (an average index level has no meaning).
- **Fed paths (ADR-003 timing as amended, ADR-005):** baseline, severely adverse, and adverse
  (halfway between them, quarter by quarter, before anchoring). The Fed's 13 quarters land on 2026
  Q3 to 2029 Q3; each is applied as the change from the Fed's starting point (2025 Q4) on top of
  the real June 2026 value: points for rates and unemployment, percentage change for house prices
  (the Fed's index is not FHFA's, so only its changes are used).
- **Contracts (control C01 for economic data):** series present, start no later than January 1990,
  no missing month except known gaps, no duplicate dates, values in range, all 51 states, FHFA
  quarters contiguous, the Fed files' columns exactly as expected with 13 consecutive quarters, and
  the Fed's history file ending exactly at 2025 Q4, the quarter before the scenarios. A failure
  stops the load before bronze is written.
- **Freshness (control C18):** each series has a maximum age; a stale series is reported in the
  run summary (alerts come in step 8) but doesn't stop the load, because old public data is still
  valid data.

## Evidence (15 November 2026, dev Airflow on the PC)

Run `manual__2026-11-15T23:34:41` of `cecl_macro_data` (`CECL_ENV=dev`), all four tasks
succeeded. After the timing decision, run `manual__2026-11-16T01:28:02` (all four tasks
succeeded; DAG paused again; no log contains the key) produced the Fed and `fed_paths` rows below,
identical to a run of the same code on the PC; the other tables were unchanged (the scheduler also ran last Wednesday's interval when the DAG was unpaused; both runs
succeeded and gave identical tables, as expected of idempotent tasks). The DAG was paused again
afterwards.

| Source | Contract | Bronze rows | Latest value | Stale |
|---|---|---|---|---|
| FRED: 5 national + 51 state series | pass (56 of 56) | 26,183 | mortgage rate week of 12 Nov 2026; national unemployment Oct 2026; states Sep 2026 | none |
| FHFA: 51 states + US | pass | 10,712 | 2026 Q2 | none |
| Fed 2026: baseline, severely adverse, history | pass (3 of 3) | 3,616 | 2029 Q1 (history to 2025 Q4) | none |

| Silver table | Rows | Notes |
|---|---|---|
| `national_monthly` | 2,648 | 6 series, January 1990 onward |
| `state_monthly` | 44,829 | unemployment and house prices, 51 states |
| `long_run_averages` | 108 | US and each state, January 1990 to June 2026 |
| `fed_paths` | 234 | 3 scenarios × 13 quarters (2026 Q3 to 2029 Q3) × 6 variables |

- **June 2026 anchors:** unemployment 4.2% (reported), mortgage rate 6.49% (average of the June
  weeks), 10-year 4.47%, 5-year 4.21%, 3-month bill 3.66%, FHFA US index 722.4 (extrapolated one
  month from May under rule L1, provisional until FHFA publishes 2026 Q3).
- **Flags:** 52 provisional months (June 2026 house prices, 51 states and the US); 52 gap-filled
  months (October 2025 unemployment, national and 51 states).
- **Checks:** the same silver tables came out of an independent run from FRED's public CSV
  download (same row counts, same anchors); no task log contains the FRED key's URL parameter; the
  DAG import check passes inside the image (both DAGs load as dev and prod and are refused without
  `CECL_ENV`; schedules clear of the blocked windows); 301 unit tests pass, coverage 100% for
  `cecl/` and 99% for `ingestion/`.
- **Sizes:** raw 2.9 MB per download; bronze and silver together under 0.4 MB.
