"""The weekly monitoring's steps on the VM (Phase 8; DAG `cecl_weekly_monitoring`), kept here so they are tested.

1. `latest_months`: each source's newest month, read from what the VM already holds (its economic tables, the
   FFIEC aggregates and the last close), so freshness costs no Databricks minutes;
2. `cecl.monitoring.needs_scoring`: score only when a new economic month has arrived since the last scored run;
3. the Databricks job `monitoring` (mode weekly) scores the live book and returns the PSI per transition;
4. `record`: the week's status (green / amber / red) and the state for next week.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from cecl import close, monitoring

SEEDED_FREDDIE_MONTH = "202606"  # the last month of the Freddie release the Phase 4 closes were built on


def _max_month(path: Path, variable: str | None = None) -> str | None:
    if not path.exists():
        return None
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=["month", "variable"] if variable else ["month"])
    if variable:
        t = t.filter(pc.equal(t["variable"], variable))
    if t.num_rows == 0:
        return None
    return str(pc.max(t["month"]).as_py())[:7].replace("-", "")


def latest_months(data_root: Path) -> dict[str, str | None]:
    root = Path(data_root)
    macro = root / "local" / "silver" / "macro"
    closes = (
        sorted(p.name for p in (root / "close").glob("*") if close.is_quarter_end(p.name))
        if (root / "close").exists()
        else []
    )
    peer = root / "local" / "silver" / "ffiec_peer_ratios.json"
    periods = sorted(json.loads(peer.read_text(encoding="utf-8")).get("periods", {})) if peer.exists() else []
    return {
        "freddie_performance": closes[-1] if closes else SEEDED_FREDDIE_MONTH,
        "fred_national": _max_month(macro / "national_monthly.parquet"),
        "fred_state_unemployment": _max_month(macro / "state_monthly.parquet", "unemployment_rate"),
        "fhfa_hpi": _max_month(macro / "state_monthly.parquet", "hpi"),
        "ffiec_call_reports": periods[-1].replace("-", "")[:6] if periods else None,
    }


ECONOMY_SOURCES = ("fred_national", "fred_state_unemployment", "fhfa_hpi")


def economy_month(latest: dict[str, str | None]) -> str | None:
    """The latest month every economic input has (FHFA's state index lags FRED by months): the month the weekly
    scoring can use, so a new month counts only when all of them have it. The same rule as notebook 69."""
    months = [latest.get(s) for s in ECONOMY_SOURCES]
    return min(m for m in months if m) if all(months) else None


def state_path(data_root: Path) -> Path:
    return Path(data_root) / "monitoring" / "weekly_state.json"


def last_scored(data_root: Path) -> str | None:
    p = state_path(data_root)
    return json.loads(p.read_text(encoding="utf-8")).get("economy_month") if p.exists() else None


def record(
    data_root: Path, today: date, latest: dict[str, str | None], scored: dict[str, Any] | None
) -> dict[str, Any]:
    """The week's monitoring record (aggregates only), saved on the VM; the state moves on only when scored."""
    fresh = monitoring.freshness(latest, today)
    rec = monitoring.weekly_status(fresh, (scored or {}).get("psi") if scored else None)
    rec |= {"date": today.isoformat(), "economy_month": economy_month(latest),
            "book_month": (scored or {}).get("book_month"), "models": (scored or {}).get("models")}  # fmt: skip
    out = Path(data_root) / "monitoring" / "weekly"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{today.isoformat()}.json").write_text(json.dumps(rec, indent=1, default=float), encoding="utf-8")
    if scored:
        state_path(data_root).write_text(json.dumps({"economy_month": economy_month(latest),
                                                     "date": today.isoformat()}), encoding="utf-8")  # fmt: skip
    return rec


def summary_line(rec: dict[str, Any]) -> str:
    stale = [k for k, v in rec["freshness"].items() if v["status"] != "green"]
    psi = (
        ", ".join(f"{k} {v:.2f}" for k, v in sorted(rec["psi"].items()))
        or "not scored (no new economic month)"
    )
    return f"status {rec['status'].upper()}; PSI: {psi}; sources not green: {stale or 'none'}"
