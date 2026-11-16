"""Economic data: download -> raw -> contract -> bronze, then bronze -> silver (Phase 1 step 7).

Called by the Airflow DAG `cecl_macro_data` (one task per source, then silver) and by
`python -m scripts.load_macro` on the PC. Every function takes the data root explicitly
(`data` on the PC, `/opt/airflow/data` in the containers) and returns a small summary dict for the
run log. Folders under the root:

    raw/macro/<source>/<run date>/      files exactly as downloaded (kept: FHFA revises history)
    local/bronze/macro_<source>/        one Parquet table per source, as published
    local/silver/macro/                 national_monthly, state_monthly, long_run_averages, fed_paths

Re-running is safe: raw files for the same run date are overwritten, and bronze and silver tables
are replaced whole, atomically (written to a temporary name, then renamed), so a failed run never
leaves half a table. A contract failure stops before bronze is written.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from cecl import macro
from ingestion import fed_scenarios, fhfa, fred, http
from ingestion import macro_contracts as mc

RESERVE_MONTH = date(2026, 6, 1)  # ADR-003: data cut-off 30 June 2026; Fed paths from July 2026
FED_TIMING = macro.SHIFTED  # ADR-003 amendment (15 Nov 2026): Fed quarter 1 becomes 2026 Q3
HISTORY = "actual"  # bronze label for the Fed's history file (its last quarter is the starting point)
LONG_RUN_START = date(1990, 1, 1)  # ADR-003 D2
FRED_PAUSE_SECONDS = 0.6  # FRED allows 120 requests a minute; ~56 series stay well inside it

SILVER_TABLES = ("national_monthly", "state_monthly", "long_run_averages", "fed_paths")


def raw_dir(root: Path, source: str, run_date: date) -> Path:
    return Path(root) / "raw" / "macro" / source / run_date.isoformat()


def bronze_path(root: Path, source: str) -> Path:
    return Path(root) / "local" / "bronze" / f"macro_{source}" / f"{source}.parquet"


def silver_path(root: Path, table: str) -> Path:
    return Path(root) / "local" / "silver" / "macro" / f"{table}.parquet"


def write_table(table: pa.Table, path: Path) -> int:
    """Replace `path` atomically. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return table.num_rows


def _save_raw(folder: Path, name: str, body: bytes) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(body)
    return name


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


# ------------------------------------------------------------------------------------------- loads


def load_fred(
    root: Path,
    run_date: date,
    key: str | None = None,
    fetch: http.Fetch | None = None,
    contract: dict[str, Any] | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """All FRED series in the contract (national, then 51 states) -> bronze macro_fred."""
    contract = contract or mc.load_contract()
    key = key if key is not None else fred.api_key()
    folder, downloaded_at = raw_dir(root, "fred", run_date), _now()
    findings, rows = [], []
    for i, rule in enumerate(mc.fred_rules(contract)):
        if i:
            pause(FRED_PAUSE_SECONDS)
        body, obs = fred.fetch_series(rule.series_id, key, rule.start, fetch)
        name = _save_raw(folder, f"{rule.series_id}.json", body)
        findings.append(mc.check_series(rule, obs, run_date))
        rows += [
            {
                "series_id": rule.series_id,
                "date": d,
                "value": v,
                "source_file": name,
                "downloaded_at": downloaded_at,
            }
            for d, v in obs
        ]
    mc.enforce(findings)
    schema = pa.schema(
        [
            ("series_id", pa.string()),
            ("date", pa.date32()),
            ("value", pa.float64()),
            ("source_file", pa.string()),
            ("downloaded_at", pa.timestamp("s", tz="UTC")),
        ]
    )
    written = write_table(pa.Table.from_pylist(rows, schema), bronze_path(root, "fred"))
    return _summary("fred", findings, written)


def load_fhfa(
    root: Path, run_date: date, fetch: http.Fetch | None = None, contract: dict[str, Any] | None = None
) -> dict[str, Any]:
    """FHFA state and US all-transactions indexes -> bronze macro_fhfa."""
    contract = contract or mc.load_contract()
    rule, get = contract["fhfa"], fetch or http.fetch
    folder, downloaded_at = raw_dir(root, "fhfa", run_date), _now()
    index: dict[str, dict[tuple[int, int], float]] = {}
    sources: dict[str, str] = {}
    for url, keep in ((rule["state_url"], contract["states"]), (rule["us_url"], [rule["us_geo"]])):
        body = get(url)
        name = _save_raw(folder, url.rsplit("/", 1)[-1], body)
        part = fhfa.parse_hpi(body.decode("utf-8-sig"), keep)
        index.update(part)
        sources.update(dict.fromkeys(part, name))
    finding = mc.check_fhfa(index, contract, run_date)
    mc.enforce([finding])
    rows = [
        {
            "geo": geo,
            "year": y,
            "quarter": q,
            "hpi": v,
            "source_file": sources[geo],
            "downloaded_at": downloaded_at,
        }
        for geo, quarters in sorted(index.items())
        for (y, q), v in sorted(quarters.items())
    ]
    schema = pa.schema(
        [
            ("geo", pa.string()),
            ("year", pa.int16()),
            ("quarter", pa.int8()),
            ("hpi", pa.float64()),
            ("source_file", pa.string()),
            ("downloaded_at", pa.timestamp("s", tz="UTC")),
        ]
    )
    written = write_table(pa.Table.from_pylist(rows, schema), bronze_path(root, "fhfa"))
    return _summary("fhfa", [finding], written)


def load_fed(
    root: Path, run_date: date, fetch: http.Fetch | None = None, contract: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The Fed's baseline and severely adverse domestic paths -> bronze macro_fed (long format)."""
    contract = contract or mc.load_contract()
    rule, get = contract["fed"], fetch or http.fetch
    folder, downloaded_at = raw_dir(root, "fed", run_date), _now()
    findings, rows = [], []
    # The history file (scenario "actual") holds the Fed's starting point for the shifted paths.
    sources = [*rule["files"].items(), (HISTORY, rule["history"]["url"])]
    for scenario, url in sources:
        body = get(url)
        name = _save_raw(folder, url.rsplit("/", 1)[-1], body)
        header, data = fed_scenarios.parse_csv(body.decode("utf-8-sig"))
        if scenario == HISTORY:
            finding = mc.check_fed_history(header, data, contract)
        else:
            finding = mc.check_fed(scenario, header, data, contract)
        findings.append(finding)
        if finding.ok:
            for r in fed_scenarios.long_rows(scenario, header, data):
                rows.append(
                    {**r, "scenario_year": rule["year"], "source_file": name, "downloaded_at": downloaded_at}
                )
    mc.enforce(findings)
    schema = pa.schema(
        [
            ("scenario", pa.string()),
            ("year", pa.int16()),
            ("quarter", pa.int8()),
            ("variable", pa.string()),
            ("value", pa.float64()),
            ("scenario_year", pa.int16()),
            ("source_file", pa.string()),
            ("downloaded_at", pa.timestamp("s", tz="UTC")),
        ]
    )
    written = write_table(pa.Table.from_pylist(rows, schema), bronze_path(root, "fed"))
    return _summary("fed", findings, written)


def replay_fetch(folder: Path) -> http.Fetch:
    """A `fetch` that reads a saved raw download instead of the internet (backfill, snapshot check).

    FRED files are saved as <series id>.json; FHFA and Fed files under the URL's file name.
    """

    def get(url: str) -> bytes:
        if url.startswith(fred.API_URL):
            from urllib.parse import parse_qs, urlparse

            name = parse_qs(urlparse(url).query)["series_id"][0] + ".json"
        else:
            name = url.rsplit("/", 1)[-1]
        path = Path(folder) / name
        if not path.exists():
            raise FileNotFoundError(f"raw file {name} is not in {folder}")
        return path.read_bytes()

    return get


def replay(root: Path, run_date: date, sources: tuple[str, ...] = ("fred", "fhfa", "fed")) -> dict[str, Any]:
    """Rebuild bronze from the raw files saved on `run_date` (no download, no key), then silver.

    Contracts run again, and freshness is judged against the original run date, so a replay
    reaches the same verdicts as the original run.
    """
    out: dict[str, Any] = {}
    for source in sources:
        get = replay_fetch(raw_dir(root, source, run_date))
        if source == "fred":
            out[source] = load_fred(root, run_date, key="replay", fetch=get, pause=lambda s: None)
        elif source == "fhfa":
            out[source] = load_fhfa(root, run_date, fetch=get)
        elif source == "fed":
            out[source] = load_fed(root, run_date, fetch=get)
        else:
            raise ValueError(f"unknown source {source!r}")
    out["silver"] = build_silver(root)
    return out


def stale_sources(summaries: dict[str, Any]) -> dict[str, Any]:
    """{series or file: days old} gathered from the loaders' summaries (for alerts and the log)."""
    stale: dict[str, Any] = {}
    for summary in summaries.values():
        if isinstance(summary, dict):
            stale.update(summary.get("stale") or {})
    return stale


def _summary(source: str, findings: list[mc.Findings], rows: int) -> dict[str, Any]:
    stale = {f.what: f.stale for f in findings if f.stale}
    latest = [f.latest for f in findings if f.latest]
    return {
        "source": source,
        "series_checked": len(findings),
        "contract": "pass",
        "bronze_rows": rows,
        "latest": max(latest).isoformat() if latest else None,
        "stale": stale,
    }


# ------------------------------------------------------------------------------------------ silver


def build_silver(
    root: Path,
    contract: dict[str, Any] | None = None,
    reserve_month: date = RESERVE_MONTH,
    timing: str = FED_TIMING,
) -> dict[str, Any]:
    """Bronze -> the four silver tables. Returns row counts, anchors and flags for the run log."""
    contract = contract or mc.load_contract()
    rules = {r.series_id: r for r in mc.fred_rules(contract)}
    fred_rows = pq.read_table(bronze_path(root, "fred")).to_pylist()
    by_series: dict[str, list[tuple[date, float | None]]] = {}
    for r in fred_rows:
        by_series.setdefault(r["series_id"], []).append((r["date"], r["value"]))

    def monthly(series_id: str) -> list[macro.MonthValue]:
        rule, obs = rules[series_id], sorted(by_series[series_id])
        if rule.frequency == "weekly":
            return macro.weekly_to_monthly(obs)
        return macro.fill_known_gaps(obs, rule.known_gaps)

    national: list[dict[str, Any]] = []
    for raw in contract["fred"]["national"]:
        for v in monthly(raw["id"]):
            national.append(_month_row(raw["name"], v, f"FRED {raw['id']}"))

    hpi = _fhfa_index(root)
    us_geo = contract["fhfa"]["us_geo"]
    hpi_monthly = {geo: _hpi_monthly(q) for geo, q in hpi.items()}
    national += [_month_row("hpi", v, f"FHFA AT {us_geo}") for v in hpi_monthly[us_geo]]

    state_rows: list[dict[str, Any]] = []
    pattern = contract["fred"]["state_unemployment"]["id_pattern"]
    for state in contract["states"]:
        sid = pattern.format(state=state)
        state_rows += [
            {"state": state, **_month_row("unemployment_rate", v, f"FRED {sid}")} for v in monthly(sid)
        ]
        state_rows += [{"state": state, **_month_row("hpi", v, "FHFA AT")} for v in hpi_monthly[state]]

    # Long-run averages (ADR-003 D2): levels for unemployment and rates; growth for house prices.
    long_run: list[dict[str, Any]] = []
    for geo, rows in [("US", national)] + [
        (s, [r for r in state_rows if r["state"] == s]) for s in contract["states"]
    ]:
        for variable in sorted({r["variable"] for r in rows}):
            values = [_as_month_value(r) for r in rows if r["variable"] == variable]
            if variable == "hpi":
                value, n = macro.long_run_growth(values, LONG_RUN_START, reserve_month)
                measure = "annual_log_growth"
            else:
                value, n = macro.long_run_average(values, LONG_RUN_START, reserve_month)
                measure = "level_mean"
            long_run.append(
                {
                    "geo": geo,
                    "variable": variable,
                    "measure": measure,
                    "value": value,
                    "start_month": LONG_RUN_START,
                    "end_month": reserve_month,
                    "months": n,
                }
            )

    # Fed paths anchored on our real values in the reserve month (ADR-003 timing).
    anchors, anchor_flags = {}, {}
    for r in national:
        if r["month"] == reserve_month:
            anchors[r["variable"]] = r["value"]
            anchor_flags[r["variable"]] = r["method"]
    fed = _fed_values(root)
    history = fed.pop(HISTORY)
    start_quarter = max(history)  # the contract makes this the quarter before the scenarios
    first_quarter = macro.add_quarters(macro.quarter_of(reserve_month), 1)
    paths = macro.fed_paths(fed, anchors, history[start_quarter], first_quarter, timing)
    path_rows = [
        {
            **p.__dict__,
            "quarter_end": macro.quarter_end_month(p.year, p.quarter),
            "anchor_month": reserve_month,
        }
        for p in paths
    ]

    tables = {
        "national_monthly": _table(national),
        "state_monthly": _table(state_rows),
        "long_run_averages": _table(long_run),
        "fed_paths": _table(path_rows),
    }
    counts = {name: write_table(t, silver_path(root, name)) for name, t in tables.items()}
    provisional = sorted(
        {
            f"{r.get('state', 'US')} {r['variable']} {r['month']:%Y-%m}"
            for r in national + state_rows
            if r["provisional"]
        }
    )
    gap_filled = sorted(
        {
            f"{r['variable']} {r['month']:%Y-%m}"
            for r in national + state_rows
            if r["method"] == macro.GAP_FILLED
        }
    )
    return {
        "silver_rows": counts,
        "reserve_month": reserve_month.isoformat(),
        "anchors": {k: round(v, 4) for k, v in sorted(anchors.items())},
        "anchor_methods": anchor_flags,
        "fed_timing": timing,
        "fed_start_quarter": f"{start_quarter[0]} Q{start_quarter[1]}",
        "severely_adverse_worst": macro.path_extremes(paths, macro.SEVERELY_ADVERSE),
        "provisional_months": len(provisional),
        "provisional_examples": provisional[:3],
        "gap_filled": sorted({g.split(" ", 1)[1] for g in gap_filled}),
        "gap_filled_series_months": len(
            [r for r in national + state_rows if r["method"] == macro.GAP_FILLED]
        ),
    }


def _month_row(variable: str, v: macro.MonthValue, source: str) -> dict[str, Any]:
    return {
        "month": v.month,
        "variable": variable,
        "value": v.value,
        "method": v.method,
        "provisional": v.provisional,
        "source": source,
    }


def _as_month_value(r: dict[str, Any]) -> macro.MonthValue:
    return macro.MonthValue(r["month"], r["value"], r["method"], r["provisional"])


def _hpi_monthly(quarters: dict[tuple[int, int], float]) -> list[macro.MonthValue]:
    """Rule M1/L1 on the full history, extended to the end of the latest quarter, kept from 1990."""
    last = max(quarters)
    months = macro.quarterly_to_monthly(quarters, extend_through=macro.quarter_end_month(*last))
    return [m for m in months if m.month >= LONG_RUN_START]


def _fhfa_index(root: Path) -> dict[str, dict[tuple[int, int], float]]:
    out: dict[str, dict[tuple[int, int], float]] = {}
    for r in pq.read_table(bronze_path(root, "fhfa")).to_pylist():
        out.setdefault(r["geo"], {})[(r["year"], r["quarter"])] = r["hpi"]
    return out


def _fed_values(root: Path) -> dict[str, dict[tuple[int, int], dict[str, float]]]:
    wanted = set(macro.FED_VARIABLES)
    out: dict[str, dict[tuple[int, int], dict[str, float]]] = {}
    for r in pq.read_table(bronze_path(root, "fed")).to_pylist():
        if r["variable"] in wanted and r["value"] is not None:  # old history rows have gaps
            out.setdefault(r["scenario"], {}).setdefault((r["year"], r["quarter"]), {})[r["variable"]] = r[
                "value"
            ]
    return out


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    if not rows:
        raise ValueError("refusing to write an empty silver table")
    return pa.Table.from_pylist(rows)
