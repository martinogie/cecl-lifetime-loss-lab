"""Phase 5 stress and sensitivity runs: what each run changes (decisions: Martins, 20 November 2026).

Every run is the 30 June 2026 reserve book, projected by model v2 and priced by the Phase 4 engine, with
exactly one thing changed from the booked set-up (Fed scenarios, 24-month forecast, 12-month reversion,
shifted Fed timing, payoffs as fitted, no LGD floor, weights 60/30/10):

- **shocks**: a custom shock on top of the Fed baseline (`scenarios.SHOCKS`), baseline only;
- **sensitivities that need a new projection**: forecast period, reversion speed, Fed timing, payoff speed,
  each under all three scenarios;
- **sensitivities priced from the booked projection**: LGD floor and scenario weights (`scripts/build_stress.py`).

The booked runs themselves come from Phase 4 (no variant suffix).
"""

from __future__ import annotations

from dataclasses import dataclass

from cecl import scenarios

ALL = scenarios.SCENARIOS
FORECAST, REVERSION = scenarios.FORECAST_MONTHS, scenarios.REVERSION_MONTHS  # booked (ADR-003)


@dataclass(frozen=True)
class Variant:
    label: str
    scenarios: tuple[str, ...] = ALL
    shock: str = ""  # a key of scenarios.SHOCKS
    forecast_months: int = FORECAST
    reversion_months: int = REVERSION
    timing: str = "shifted"  # "calendar": the Fed's own quarter dates (ADR-003 amendment, sensitivity only)
    payoff_multiplier: float = 1.0


VARIANTS: dict[str, Variant] = {
    **{k: Variant(f"Shock: {k}", ("baseline",), shock=k) for k in scenarios.SHOCKS},
    "forecast_12": Variant("Forecast period 12 months", forecast_months=12),
    "forecast_36": Variant("Forecast period 36 months", forecast_months=36),
    "reversion_6": Variant("Reversion 6 months", reversion_months=6),
    "reversion_24": Variant("Reversion 24 months", reversion_months=24),
    "timing_calendar": Variant("Fed timing: calendar quarters", timing="calendar"),
    "payoff_08": Variant("Payoffs x0.8", payoff_multiplier=0.8),
    "payoff_12": Variant("Payoffs x1.2", payoff_multiplier=1.2),
}
LGD_FLOORS = (0.05, 0.10)  # severity floor on loans that lose (pricing only)
WEIGHT_SETS = {  # baseline / adverse / severely adverse (ADR-005 booked: 60/30/10)
    "80/15/5": (0.80, 0.15, 0.05),
    "70/20/10": (0.70, 0.20, 0.10),
    "60/30/10 (booked)": (0.60, 0.30, 0.10),
    "50/40/10": (0.50, 0.40, 0.10),
    "50/35/15": (0.50, 0.35, 0.15),
    "40/40/20": (0.40, 0.40, 0.20),
}
FED_TABLE_CALENDAR = "fed_paths_calendar"  # silver: the 2026 paths on the Fed's own quarter dates


def tag(book_ym: str, economy_ym: str, scenario: str, variant: str = "") -> str:
    """File tag of one projected book: the booked runs keep Phase 4's names."""
    base = f"{book_ym}_{economy_ym}_{scenario}"
    return f"{base}__{variant}" if variant else base


def check() -> None:
    """Every variant changes something valid (run in the tests and before a job)."""
    for name, v in VARIANTS.items():
        if v.shock and v.shock not in scenarios.SHOCKS:
            raise ValueError(f"{name}: unknown shock {v.shock}")
        if v.timing not in ("shifted", "calendar"):
            raise ValueError(f"{name}: timing {v.timing}")
        if not set(v.scenarios) <= set(ALL):
            raise ValueError(f"{name}: scenarios {v.scenarios}")
    for name, w in WEIGHT_SETS.items():
        if abs(sum(w) - 1.0) > 1e-9:
            raise ValueError(f"weights {name} do not add up to 1")
