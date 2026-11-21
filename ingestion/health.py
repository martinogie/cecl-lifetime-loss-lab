"""The weekly health check's judgement (control C18), from the run log alone.

Problems it reports (each becomes one alert line; none of them fails the check, because the check's
job is to tell someone, not to stop anything):
- a pipeline that should run weekly has no successful run in the last MAX_AGE_DAYS days;
- a pipeline's most recent run failed;
- the latest successful run reported stale sources.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

WEEKLY = ("macro",)  # pipelines with a weekly schedule (Freddie loads are quarterly, by hand)
MAX_AGE_DAYS = 8  # one weekly run may be late by a day before it counts as missing
# Early failure records (16 Nov 2026, before the callback named its pipeline) used the DAG id.
ALIASES = {
    "cecl_macro_data": "macro",
    "cecl_health_check": "health_check",
    "cecl_ffiec_call_reports": "ffiec",
}


def problems(
    records: Iterable[Mapping[str, Any]], now: datetime, max_age_days: int = MAX_AGE_DAYS
) -> list[str]:
    by_pipeline: dict[str, list[Mapping[str, Any]]] = {}
    for r in records:
        name = ALIASES.get(str(r["pipeline"]), str(r["pipeline"]))
        by_pipeline.setdefault(name, []).append(r)
    found: list[str] = []
    for name in WEEKLY:
        runs = sorted(by_pipeline.get(name, []), key=lambda r: str(r["started_at"]))
        ok = [r for r in runs if r["status"] == "success"]
        if not ok:
            found.append(f"{name}: no successful run on record")
        else:
            age = now - datetime.fromisoformat(str(ok[-1]["started_at"]))
            if age.days >= max_age_days:
                found.append(f"{name}: last successful run {age.days} days ago")
            if ok[-1].get("stale"):
                found.append(f"{name}: stale sources in the last run: {sorted(ok[-1]['stale'])}")
    for name, runs in sorted(by_pipeline.items()):
        last = max(runs, key=lambda r: str(r["started_at"]))
        if last["status"] == "failed":
            found.append(f"{name}: the latest run failed ({last['run_id']})")
    return found
