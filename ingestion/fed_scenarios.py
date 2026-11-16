"""Federal Reserve supervisory scenarios (public CSV files, no key): domestic variables by quarter."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from ingestion.macro_contracts import parse_quarter


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """CSV text -> (header, rows), dropping blank lines. A byte-order mark is removed."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("empty Fed scenario file")
    return [c.strip() for c in rows[0]], rows[1:]


def values(
    header: Sequence[str], rows: Sequence[Sequence[str]], columns: Iterable[str]
) -> dict[tuple[int, int], dict[str, float]]:
    """{(year, quarter): {column: value}} for the chosen columns (already contract-checked)."""
    position = {name: i for i, name in enumerate(header)}
    chosen = list(columns)
    return {parse_quarter(r[1]): {c: float(r[position[c]]) for c in chosen} for r in rows}


def long_rows(scenario: str, header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[dict[str, object]]:
    """Every variable as one row per quarter (bronze keeps them all, used by the models or not)."""
    out: list[dict[str, object]] = []
    for r in rows:
        year, quarter = parse_quarter(r[1])
        for name, raw in zip(header[2:], r[2:], strict=False):
            text = raw.strip()
            out.append(
                {
                    "scenario": scenario,
                    "year": year,
                    "quarter": quarter,
                    "variable": name,
                    "value": float(text) if text else None,
                }
            )
    return out
