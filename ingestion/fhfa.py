"""FHFA all-transactions house price indexes (public files, no key).

Both files are headerless CSV: geography, year, quarter, index. The state file holds 50 states and
DC; the US file holds the nine census divisions and the US ("USA"), of which only the US is kept.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable


def parse_hpi(text: str, keep: Iterable[str] | None = None) -> dict[str, dict[tuple[int, int], float]]:
    """CSV text -> {geo: {(year, quarter): index}}. Lines without a numeric year (a header, if FHFA
    ever adds one) are skipped; a duplicate (geo, year, quarter) raises ValueError."""
    wanted = set(keep) if keep is not None else None
    out: dict[str, dict[tuple[int, int], float]] = {}
    for line_no, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row or not row[1].strip().isdigit():
            continue
        if len(row) < 4:
            raise ValueError(f"line {line_no}: expected 4 columns, got {len(row)}")
        geo = row[0].strip()
        if wanted is not None and geo not in wanted:
            continue
        key = (int(row[1]), int(row[2]))
        if key[1] not in (1, 2, 3, 4):
            raise ValueError(f"line {line_no}: quarter must be 1-4")
        series = out.setdefault(geo, {})
        if key in series:
            raise ValueError(f"line {line_no}: duplicate {geo} {key[0]}Q{key[1]}")
        series[key] = float(row[3])
    return out
