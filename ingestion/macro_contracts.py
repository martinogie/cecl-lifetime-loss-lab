"""Data contracts for the public economic series (contract: ingestion/contracts/macro_sources.yml).

Each check returns a `Findings` with errors (the load stops: `enforce` raises ContractViolation
before bronze is written) and `stale` notes (freshness, control C18: reported, not enforced). Same
rule as the Freddie contracts: there is no "warn only" mode for a broken file.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ingestion.data_contracts import ContractViolation

CONTRACT_PATH = Path(__file__).with_name("contracts") / "macro_sources.yml"
FREQUENCIES = ("monthly", "weekly", "quarterly")
WEEK_GAP_DAYS = (5, 9)  # Freddie Mac's weekly survey shifts by a day or two around holidays


@dataclass(frozen=True)
class SeriesRule:
    series_id: str
    name: str
    frequency: str
    min: float
    max: float
    start: date
    max_age_days: int
    known_gaps: tuple[date, ...] = ()


@dataclass
class Findings:
    what: str
    errors: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    latest: date | None = None
    rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "what": self.what,
            "ok": self.ok,
            "rows": self.rows,
            "latest": self.latest.isoformat() if self.latest else None,
            "errors": self.errors,
            "stale": self.stale,
        }


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        contract: dict[str, Any] = yaml.safe_load(f)
    return contract


def _rule(raw: Mapping[str, Any], series_id: str, start: str) -> SeriesRule:
    if raw["frequency"] not in FREQUENCIES:
        raise ValueError(f"{series_id}: unknown frequency {raw['frequency']!r}")
    return SeriesRule(
        series_id=series_id,
        name=raw["name"],
        frequency=raw["frequency"],
        min=float(raw["min"]),
        max=float(raw["max"]),
        start=date.fromisoformat(start),
        max_age_days=int(raw["max_age_days"]),
        known_gaps=tuple(date.fromisoformat(g) for g in raw.get("known_gaps", [])),
    )


def fred_rules(contract: Mapping[str, Any]) -> list[SeriesRule]:
    """Every FRED series in the contract: the national ones, then one per state."""
    fred = contract["fred"]
    rules = [_rule(r, r["id"], fred["start"]) for r in fred["national"]]
    state = fred["state_unemployment"]
    rules += [_rule(state, state["id_pattern"].format(state=s), fred["start"]) for s in contract["states"]]
    return rules


def _period_end(d: date, frequency: str) -> date:
    if frequency == "weekly":
        return d
    if frequency == "quarterly":
        last_month = ((d.month - 1) // 3) * 3 + 3
        return date(d.year, last_month, calendar.monthrange(d.year, last_month)[1])
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _freshness(f: Findings, latest: date, frequency: str, max_age_days: int, run_date: date) -> None:
    age = (run_date - _period_end(latest, frequency)).days
    if age > max_age_days:
        f.stale.append(f"latest value {latest.isoformat()} is {age} days old (max {max_age_days})")


def check_series(
    rule: SeriesRule, observations: Sequence[tuple[date, float | None]], run_date: date
) -> Findings:
    """One FRED series: start, frequency, gaps, duplicates, range, freshness."""
    f = Findings(rule.series_id, rows=len(observations))
    if not observations:
        f.errors.append("no observations")
        return f
    dates = [d for d, _ in observations]
    if len(set(dates)) != len(dates):
        f.errors.append("duplicate dates")
    if dates != sorted(dates):
        f.errors.append("dates are not in order")
        return f
    # Compared by month: a weekly series' first reading of January 1990 is dated 5 January.
    if (dates[0].year, dates[0].month) > (rule.start.year, rule.start.month):
        f.errors.append(f"starts {dates[0].isoformat()}, after the required {rule.start:%Y-%m}")

    gaps = set(rule.known_gaps)
    if rule.frequency == "monthly":
        expected = (dates[-1].year - dates[0].year) * 12 + dates[-1].month - dates[0].month + 1
        if any(d.day != 1 for d in dates):
            f.errors.append("monthly dates must be the first of the month")
        if len(set(dates)) != expected:
            f.errors.append(f"{expected - len(set(dates))} month(s) missing between first and last")
    elif rule.frequency == "weekly":
        lo, hi = WEEK_GAP_DAYS
        bad = [(a, b) for a, b in zip(dates, dates[1:], strict=False) if not lo <= (b - a).days <= hi]
        if bad:
            a, b = bad[0]
            f.errors.append(f"{len(bad)} weekly gap(s) outside {lo}-{hi} days, first {a} to {b}")

    empty = [d for d, v in observations if v is None]
    unexpected = [d for d in empty if d not in gaps]
    if unexpected:
        shown = ", ".join(d.isoformat() for d in unexpected[:3])
        f.errors.append(f"{len(unexpected)} empty value(s) not in known_gaps, e.g. {shown}")
    out_of_range = [v for _, v in observations if v is not None and not rule.min <= v <= rule.max]
    if out_of_range:
        f.errors.append(f"{len(out_of_range)} value(s) outside {rule.min}-{rule.max}")
    if empty and dates[-1] in empty:
        f.errors.append("the latest observation is empty")

    with_values = [d for d, v in observations if v is not None]
    if with_values:
        f.latest = with_values[-1]
        _freshness(f, f.latest, rule.frequency, rule.max_age_days, run_date)
    return f


def check_fhfa(
    index: Mapping[str, Mapping[tuple[int, int], float]],
    contract: Mapping[str, Any],
    run_date: date,
) -> Findings:
    """FHFA quarterly indexes: every state (and the US), contiguous quarters, range, start, age."""
    rule = contract["fhfa"]
    expected_geos = set(contract["states"]) | {rule["us_geo"]}
    f = Findings("fhfa_hpi_at", rows=sum(len(q) for q in index.values()))
    missing, extra = expected_geos - set(index), set(index) - expected_geos
    if missing:
        f.errors.append(f"missing geographies: {', '.join(sorted(missing))}")
    if extra:
        f.errors.append(f"unexpected geographies: {', '.join(sorted(extra))}")
    start = date.fromisoformat(rule["start"])
    latest: set[tuple[int, int]] = set()
    for geo, quarters in sorted(index.items()):
        if not quarters:
            f.errors.append(f"{geo}: no quarters")
            continue
        keys = sorted(quarters)
        ordinal = [y * 4 + q - 1 for y, q in keys]
        if ordinal[-1] - ordinal[0] + 1 != len(keys):
            f.errors.append(f"{geo}: {ordinal[-1] - ordinal[0] + 1 - len(keys)} quarter(s) missing")
        if (keys[0][0], keys[0][1]) > (start.year, (start.month - 1) // 3 + 1):
            f.errors.append(f"{geo}: starts {keys[0][0]}Q{keys[0][1]}, after {start.isoformat()}")
        bad = [v for v in quarters.values() if not rule["min"] <= v <= rule["max"]]
        if bad:
            f.errors.append(f"{geo}: {len(bad)} value(s) outside {rule['min']}-{rule['max']}")
        latest.add(keys[-1])
    if len(latest) > 1:
        f.errors.append(f"geographies end in different quarters: {sorted(latest)}")
    if latest:
        y, q = max(latest)
        f.latest = date(y, 3 * q - 2, 1)
        _freshness(f, f.latest, "quarterly", int(rule["max_age_days"]), run_date)
    return f


def check_fed(
    scenario: str, header: Sequence[str], rows: Sequence[Sequence[str]], contract: Mapping[str, Any]
) -> Findings:
    """One Fed scenario CSV: exact columns, scenario name, 13 consecutive quarters, numbers, ranges."""
    rule = contract["fed"]
    f = Findings(f"fed_{scenario}", rows=len(rows))
    if list(header) != list(rule["columns"]):
        f.errors.append("columns differ from the contract (a new Fed layout needs a contract update)")
        return f
    names = {r[0] for r in rows}
    if names != {rule["scenario_names"][scenario]}:
        f.errors.append(f"scenario names {sorted(names)}, expected {rule['scenario_names'][scenario]!r}")
    if len(rows) != rule["quarters"]:
        f.errors.append(f"{len(rows)} quarters, expected {rule['quarters']}")
    try:
        quarters = [parse_quarter(r[1]) for r in rows]
    except ValueError as e:
        f.errors.append(str(e))
        return f
    first = parse_quarter(rule["first_quarter"])
    expected = [_quarter_add(first, i) for i in range(len(rows))]
    if quarters != expected:
        f.errors.append(f"quarters must run consecutively from {rule['first_quarter']}")
    position = {name: i for i, name in enumerate(rule["columns"])}
    for column, (lo, hi) in rule["ranges"].items():
        for r in rows:
            try:
                value = float(r[position[column]])
            except (ValueError, IndexError):
                f.errors.append(f"{r[1]}: {column!r} is not a number")
                continue
            if not lo <= value <= hi:
                f.errors.append(f"{r[1]}: {column!r} = {value} outside {lo}-{hi}")
    if quarters:
        y, q = quarters[-1]
        f.latest = date(y, 3 * q - 2, 1)
    return f


def check_fed_history(
    header: Sequence[str], rows: Sequence[Sequence[str]], contract: Mapping[str, Any]
) -> Findings:
    """The Fed's history file: exact columns, consecutive quarters ending just before the scenarios.

    Only the last quarter (the starting point the paths are measured from) is range-checked: older
    rows legitimately have empty columns (series that start later).
    """
    rule, hist = contract["fed"], contract["fed"]["history"]
    f = Findings("fed_history", rows=len(rows))
    if list(header) != list(rule["columns"]):
        f.errors.append("columns differ from the contract (a new Fed layout needs a contract update)")
        return f
    if not rows:
        f.errors.append("no rows")
        return f
    names = {r[0] for r in rows}
    if names != {hist["scenario_name"]}:
        f.errors.append(f"scenario names {sorted(names)}, expected {hist['scenario_name']!r}")
    try:
        quarters = [parse_quarter(r[1]) for r in rows]
    except ValueError as e:
        f.errors.append(str(e))
        return f
    if quarters != [_quarter_add(quarters[0], i) for i in range(len(quarters))]:
        f.errors.append("quarters must run consecutively")
    last = parse_quarter(hist["last_quarter"])
    if quarters[-1] != last:
        f.errors.append(f"last quarter {quarters[-1]}, expected {hist['last_quarter']}")
    if _quarter_add(last, 1) != parse_quarter(rule["first_quarter"]):
        f.errors.append("the history's last quarter must be the quarter before the scenarios start")
    position = {name: i for i, name in enumerate(rule["columns"])}
    final = rows[-1]
    for column, (lo, hi) in rule["ranges"].items():
        try:
            value = float(final[position[column]])
        except (ValueError, IndexError):
            f.errors.append(f"{final[1]}: {column!r} is not a number")
            continue
        if not lo <= value <= hi:
            f.errors.append(f"{final[1]}: {column!r} = {value} outside {lo}-{hi}")
    y, q = quarters[-1]
    f.latest = date(y, 3 * q - 2, 1)
    return f


def parse_quarter(text: str) -> tuple[int, int]:
    """'2026 Q1' -> (2026, 1)."""
    parts = text.strip().split()
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("Q1", "Q2", "Q3", "Q4"):
        raise ValueError(f"not a quarter like '2026 Q1': {text!r}")
    return int(parts[0]), int(parts[1][1])


def _quarter_add(yq: tuple[int, int], n: int) -> tuple[int, int]:
    index = yq[0] * 4 + yq[1] - 1 + n
    return index // 4, index % 4 + 1


def enforce(findings: Sequence[Findings]) -> None:
    """Raise ContractViolation if any check has an error (the load stops before bronze)."""
    broken = [f for f in findings if not f.ok]
    if broken:
        lines = [f"{f.what}: {e}" for f in broken for e in f.errors]
        raise ContractViolation("economic data contract failed:\n  " + "\n  ".join(lines[:40]))
