"""Household-stress series for model v2 (ADR-009 Amendment 3; plan decision D3), national, from FRED.

Downloaded from FRED's public CSV endpoint (no API key is needed or sent), checked against the
contract (`fred_household` in contracts/macro_sources.yml), saved raw, and written to their own silver
table so v1's economic tables stay exactly as they were:

    data/raw/macro/household/<run date>/<series>.csv      (as downloaded)
    data/local/silver/macro/household_monthly.parquet     (month, variable, value, source)

Variables (all known at the end of the month they are dated; the feature build reads month t-1):
- cpi_inflation_12: CPI change over 12 months, % (CPIAUCSL);
- real_income_growth_12: real disposable income change over 12 months, % (DSPIC96);
- card_delinquency: bank credit-card delinquency rate, % (DRCCLACBS, quarterly). A month carries the
  latest quarter that has *ended* by then, so no month sees a quarter before it is over;
- cpi_index, real_disposable_income: the levels (for the Phase 4 reversion, if a candidate is kept).
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa

from cecl import macro
from ingestion import http
from ingestion import macro_build as mb
from ingestion import macro_contracts as mc

# FRED's CSV endpoint stalls on non-browser clients; a generic browser-style name, no personal details.
USER_AGENT = "Mozilla/5.0 (compatible; cecl-macro-loader/1.0)"
TABLE = "household_monthly"


def rules(contract: dict[str, Any]) -> list[mc.SeriesRule]:
    section = contract["fred_household"]
    return [mc._rule(r, r["id"], r.get("start", section["start"])) for r in section["series"]]


def fetch(url: str, timeout: int = 60) -> bytes:
    """FRED's CSV endpoint stalls Python's own HTTP client (it answers curl at once), so the download
    uses curl, which ships with Windows 10+, Git Bash and the Airflow image. Public file, no key."""
    import subprocess

    done = subprocess.run(["curl", "-sS", "-f", "-L", "--retry", "3", "-m", str(timeout), "-A", USER_AGENT, url],
                          capture_output=True, check=False)  # fmt: skip
    if done.returncode != 0 or not done.stdout:
        raise http.DownloadError(f"download failed (curl exit {done.returncode}): {url}")
    return done.stdout


def parse_csv(body: bytes) -> list[tuple[date, float | None]]:
    """FRED's CSV (a date column, then the value; '.' or blank = missing) -> [(date, value)]."""
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows or len(rows[0]) < 2:
        raise ValueError("not a FRED CSV")
    out = []
    for r in rows[1:]:
        if not r:
            continue
        raw = r[1].strip()
        out.append((date.fromisoformat(r[0].strip()), None if raw in ("", ".") else float(raw)))
    return out


def change_12(values: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Percent change over 12 months: 100 x (value / value 12 months earlier - 1)."""
    by_month = dict(values)
    return [(m, 100.0 * (v / by_month[macro.add_months(m, -12)] - 1.0))
            for m, v in values if macro.add_months(m, -12) in by_month]  # fmt: skip


def quarter_ended_by(quarters: list[tuple[date, float]], through: date) -> list[tuple[date, float]]:
    """Monthly values from a quarterly series dated at the quarter's first day: each month carries the
    latest quarter whose last month is that month or earlier."""
    out: list[tuple[date, float]] = []
    q = sorted(quarters)
    if not q:
        return out
    month, i, current = macro.add_months(q[0][0], 2), 0, None
    while month <= through:
        while i < len(q) and macro.add_months(q[i][0], 2) <= month:
            current = q[i][1]
            i += 1
        out.append((month, float(current)))  # type: ignore[arg-type]
        month = macro.add_months(month, 1)
    return out


def build(
    series: dict[str, list[tuple[date, float | None]]], gaps_of: dict[str, tuple[date, ...]] | None = None
) -> list[dict[str, Any]]:
    """The silver rows from the downloaded series (keyed by FRED id)."""
    gaps_of = gaps_of or {}

    def clean(sid: str) -> list[tuple[date, float]]:
        return [(d, v) for d, v in series[sid] if v is not None]

    def monthly(sid: str) -> list[tuple[date, float]]:
        # a known gap (October 2025, no survey) is filled on a straight line, as in v1's silver
        gaps = gaps_of.get(sid, ())
        return [(v.month, v.value) for v in macro.fill_known_gaps(series[sid], gaps)]

    cpi, income, card = monthly("CPIAUCSL"), monthly("DSPIC96"), clean("DRCCLACBS")
    last = max(cpi[-1][0], income[-1][0])
    rows: list[tuple[str, list[tuple[date, float]], str]] = [
        ("cpi_index", cpi, "FRED CPIAUCSL"),
        ("cpi_inflation_12", change_12(cpi), "FRED CPIAUCSL, 12-month % change"),
        ("real_disposable_income", income, "FRED DSPIC96"),
        ("real_income_growth_12", change_12(income), "FRED DSPIC96, 12-month % change"),
        ("card_delinquency", quarter_ended_by(card, last), "FRED DRCCLACBS, latest quarter ended"),
    ]
    return [{"month": m, "variable": name, "value": float(v), "source": src}
            for name, values, src in rows for m, v in values]  # fmt: skip


def load(root: Path, run_date: date, get: http.Fetch | None = None,
         contract: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """Download, check (a failure stops the load), save raw and write the silver table."""
    contract = contract or mc.load_contract()
    section, get = contract["fred_household"], get or fetch
    folder = mb.raw_dir(root, "household", run_date)
    findings, series = [], {}
    for rule in rules(contract):
        body = get(section["csv_url"].format(id=rule.series_id, start=section["start"]))
        mb._save_raw(folder, f"{rule.series_id}.csv", body)
        obs = parse_csv(body)
        findings.append(mc.check_series(rule, obs, run_date))
        series[rule.series_id] = obs
    mc.enforce(findings)
    rows = build(series, {r.series_id: r.known_gaps for r in rules(contract)})
    schema = pa.schema([("month", pa.date32()), ("variable", pa.string()), ("value", pa.float64()),
                        ("source", pa.string())])  # fmt: skip
    written = mb.write_table(pa.Table.from_pylist(rows, schema), mb.silver_path(root, TABLE))
    return {"rows": written, "findings": [f.as_dict() for f in findings],
            "last_month": {v: max(r["month"] for r in rows if r["variable"] == v).isoformat()
                           for v in sorted({r["variable"] for r in rows})}}  # fmt: skip


if __name__ == "__main__":
    import json
    import sys

    from cecl import environment

    environment.current_env()
    print(json.dumps(load(Path("data"), date.today()), indent=1, default=str))
    sys.exit(0)
