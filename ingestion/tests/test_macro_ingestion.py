"""Unit tests for the economic-data loaders: contracts, FRED, FHFA, Fed, downloads, bronze, silver.

Offline: every download goes through a fake `fetch` that serves synthetic data built here. The
contract is the real YAML with only two made-up states, so the tests stay small.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from cecl import macro
from ingestion import fed_scenarios, fhfa, fred, http, macro_build
from ingestion import macro_contracts as mc
from ingestion.data_contracts import ContractViolation

RUN = date(2026, 11, 15)
STATES = ["AA", "BB"]


def small_contract() -> dict[str, Any]:
    contract = mc.load_contract()
    contract["states"] = list(STATES)
    return contract


# ------------------------------------------------------------------ synthetic sources


def monthly_obs(last: date = date(2026, 10, 1), gap: date | None = date(2025, 10, 1), level: float = 5.0):
    out, m = [], date(1990, 1, 1)
    while m <= last:
        out.append((m, None if m == gap else level + (m.month % 3) / 10))
        m = macro.add_months(m, 1)
    return out


def weekly_obs(last: date = date(2026, 11, 12)):
    out, d = [], date(1990, 1, 5)
    while d <= last:
        out.append((d, 6.5))
        d += timedelta(days=7)
    return out


def fred_body(obs) -> bytes:
    return json.dumps(
        {"observations": [{"date": d.isoformat(), "value": "." if v is None else str(v)} for d, v in obs]}
    ).encode()


def fhfa_text(geos, last=(2026, 2)) -> str:
    lines = []
    for geo in geos:
        y, q, value = 1989, 1, 100.0
        while (y, q) <= last:
            lines.append(f"{geo},{y},{q},{value:.2f}")
            value *= 1.01
            y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return "\n".join(lines) + "\n"


def fed_text(scenario_name: str, contract: dict[str, Any], shock: float = 0.0) -> str:
    cols = contract["fed"]["columns"]
    rows = [",".join(cols)]
    y, q = 2026, 1
    for i in range(13):
        values = {c: 3.0 for c in cols[2:]}
        values["Unemployment rate"] = 4.5 + shock * i / 12
        values["House Price Index (Level)"] = 300.0 * (1 - shock * i / 100)
        rows.append(",".join([scenario_name, f"{y} Q{q}"] + [str(values[c]) for c in cols[2:]]))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return "\n".join(rows) + "\n"


def fed_history_text(contract: dict[str, Any], last=(2025, 4), name: str = "Actual") -> str:
    """Eight quarters of the Fed's history file, ending at its starting point (2025 Q4)."""
    cols = contract["fed"]["columns"]
    rows = [",".join(cols)]
    for i in range(8):
        y, q = macro.add_quarters(last, i - 7)
        values = {c: "3.0" for c in cols[2:]}
        values["Unemployment rate"], values["House Price Index (Level)"] = "4.5", "300.0"
        if i == 0:
            values["Mortgage rate"] = ""  # old history rows can be empty
        rows.append(",".join([name, f"{y} Q{q}"] + [values[c] for c in cols[2:]]))
    return "\n".join(rows) + "\n"


def fake_fetch(contract: dict[str, Any], overrides: dict[str, bytes] | None = None):
    overrides = overrides or {}

    def get(url: str) -> bytes:
        for key, body in overrides.items():
            if key in url:
                return body
        if url.startswith(fred.API_URL):
            sid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["series_id"][0]
            if sid == "MORTGAGE30US":
                return fred_body(weekly_obs())
            # only unemployment has the October 2025 gap (no survey); rates were published
            gap = date(2025, 10, 1) if sid.endswith("UR") or sid == "UNRATE" else None
            return fred_body(monthly_obs(level=4.0, gap=gap))
        if url == contract["fhfa"]["state_url"]:
            return fhfa_text(STATES).encode()
        if url == contract["fhfa"]["us_url"]:
            return (fhfa_text(["USA", "DV_PAC"])).encode()
        if url == contract["fed"]["history"]["url"]:
            return fed_history_text(contract).encode()
        for scenario, file_url in contract["fed"]["files"].items():
            if url == file_url:
                shock = 0.0 if scenario == "baseline" else 6.0
                return fed_text(contract["fed"]["scenario_names"][scenario], contract, shock).encode()
        raise AssertionError(f"unexpected url {url}")

    return get


# ------------------------------------------------------------------ contracts


def rule(**kw) -> mc.SeriesRule:
    base = dict(
        series_id="UNRATE",
        name="unemployment_rate",
        frequency="monthly",
        min=1.0,
        max=30.0,
        start=date(1990, 1, 1),
        max_age_days=50,
        known_gaps=(date(2025, 10, 1),),
    )
    base.update(kw)
    return mc.SeriesRule(**base)  # type: ignore[arg-type]


def test_fred_rules_cover_national_and_every_state() -> None:
    rules = mc.fred_rules(small_contract())
    ids = [r.series_id for r in rules]
    assert ids[:5] == ["UNRATE", "MORTGAGE30US", "GS10", "GS5", "TB3MS"]
    assert ids[5:] == ["AAUR", "BBUR"]
    assert len(mc.fred_rules(mc.load_contract())) == 5 + 51


def test_check_series_passes_with_known_gap_and_flags_staleness() -> None:
    f = mc.check_series(rule(), monthly_obs(), RUN)
    assert f.ok and f.latest == date(2026, 10, 1) and not f.stale
    stale = mc.check_series(rule(), monthly_obs(last=date(2026, 6, 1)), RUN)
    assert stale.ok and stale.stale  # old data is reported, not a contract error


@pytest.mark.parametrize(
    "obs, message",
    [
        (monthly_obs(gap=date(2024, 3, 1)), "not in known_gaps"),
        ([o for o in monthly_obs() if o[0] != date(2020, 5, 1)], "missing between"),
        ([(date(1995, 1, 1), 5.0), (date(1995, 2, 1), 5.0)], "after the required"),
        (monthly_obs() + [(date(2026, 11, 1), 99.0)], "outside"),
        (monthly_obs() + [(date(2026, 10, 1), 5.0)], "duplicate"),
        (list(reversed(monthly_obs())), "not in order"),
        ([], "no observations"),
        (monthly_obs()[:-1] + [(date(2026, 10, 1), None)], "latest observation is empty"),
        ([(date(1990, 1, 2), 5.0)], "first of the month"),
    ],
)
def test_check_series_errors(obs, message) -> None:
    f = mc.check_series(rule(), obs, RUN)
    assert not f.ok and any(message in e for e in f.errors), f.errors


def test_check_series_weekly_gaps() -> None:
    weekly = rule(series_id="MORTGAGE30US", frequency="weekly", max_age_days=14, known_gaps=())
    assert mc.check_series(weekly, weekly_obs(), RUN).ok
    holed = [o for o in weekly_obs() if o[0] != date(2010, 1, 8)]
    f = mc.check_series(weekly, holed, RUN)
    assert any("weekly gap" in e for e in f.errors)


def test_check_fhfa() -> None:
    contract = small_contract()
    index = fhfa.parse_hpi(fhfa_text(STATES + ["USA"]))
    assert mc.check_fhfa(index, contract, RUN).ok
    del index["BB"][(2000, 1)]
    index["AA"][(2026, 3)] = 1.0e6
    index["ZZ"] = {}
    f = mc.check_fhfa(index, contract, RUN)
    joined = " ".join(f.errors)
    for message in (
        "BB: 1 quarter(s) missing",
        "unexpected geographies: ZZ",
        "outside",
        "different quarters",
    ):
        assert message in joined
    del index["USA"]
    assert "missing geographies: USA" in " ".join(mc.check_fhfa(index, contract, RUN).errors)
    late = fhfa.parse_hpi(fhfa_text(STATES + ["USA"], last=(2025, 4)))
    assert mc.check_fhfa(late, contract, RUN).stale


def test_check_fed() -> None:
    contract = small_contract()
    header, rows = fed_scenarios.parse_csv(fed_text("Supervisory Baseline", contract))
    assert mc.check_fed("baseline", header, rows, contract).ok
    assert "columns differ" in mc.check_fed("baseline", header[:-1], rows, contract).errors[0]
    f = mc.check_fed("severely_adverse", header, rows[:12], contract)
    assert any("scenario names" in e for e in f.errors) and any("12 quarters" in e for e in f.errors)
    bad = [list(r) for r in rows]
    bad[0][header.index("Unemployment rate")] = "x"
    bad[1][header.index("Mortgage rate")] = "99"
    bad[2][1] = "2026 Q4"
    errors = " ".join(mc.check_fed("baseline", header, bad, contract).errors)
    assert "not a number" in errors and "outside" in errors and "consecutively" in errors
    bad[3][1] = "2026-07"
    assert "not a quarter" in " ".join(mc.check_fed("baseline", header, bad, contract).errors)


def test_check_fed_history() -> None:
    contract = small_contract()
    header, rows = fed_scenarios.parse_csv(fed_history_text(contract))
    f = mc.check_fed_history(header, rows, contract)
    assert f.ok and f.latest == date(2025, 10, 1)
    assert "columns differ" in mc.check_fed_history(header[:-1], rows, contract).errors[0]
    assert mc.check_fed_history(header, [], contract).errors == ["no rows"]
    early = fed_scenarios.parse_csv(fed_history_text(contract, last=(2025, 3), name="Old"))[1]
    errors = " ".join(mc.check_fed_history(header, early, contract).errors)
    assert "scenario names" in errors and "last quarter" in errors
    bad = [list(r) for r in rows]
    bad[-1][header.index("Unemployment rate")] = "x"
    bad[-1][header.index("Mortgage rate")] = "99"
    bad[3][1] = "2030 Q1"
    errors = " ".join(mc.check_fed_history(header, bad, contract).errors)
    assert "not a number" in errors and "outside" in errors and "consecutively" in errors
    bad[3][1] = "soon"
    assert "not a quarter" in " ".join(mc.check_fed_history(header, bad, contract).errors)
    moved = {**contract, "fed": {**contract["fed"], "first_quarter": "2026 Q2"}}
    assert "quarter before" in " ".join(mc.check_fed_history(header, rows, moved).errors)


def test_fed_2025_contract_section() -> None:
    """The 2025 scenarios (31 December 2025 reserve) are checked against their own contract section."""
    contract = small_contract()
    assert contract["fed_2025"]["columns"] == contract["fed"]["columns"]  # same Fed layout
    header, rows = fed_scenarios.parse_csv(fed_text("Supervisory Baseline", contract))  # dated from 2026 Q1
    errors = " ".join(mc.check_fed("baseline", header, rows, contract, "fed_2025").errors)
    assert "consecutively from 2025 Q1" in errors
    header, hist = fed_scenarios.parse_csv(fed_history_text(contract, last=(2024, 4)))
    f = mc.check_fed_history(header, hist, contract, "fed_2025")
    assert f.ok and f.what == "fed_2025_history"
    assert not mc.check_fed_history(header, hist, contract).ok  # the 2026 section expects 2025 Q4


def test_enforce_raises_with_every_error() -> None:
    good, bad = mc.Findings("a"), mc.Findings("b", errors=["broken"])
    mc.enforce([good])
    with pytest.raises(ContractViolation, match="b: broken"):
        mc.enforce([good, bad])
    assert bad.as_dict()["ok"] is False


# ------------------------------------------------------------------ sources


def test_fred_api_key_required(monkeypatch) -> None:
    monkeypatch.delenv(fred.KEY_VAR, raising=False)
    with pytest.raises(fred.MissingSecret, match="FRED_API_KEY"):
        fred.api_key()
    monkeypatch.setenv(fred.KEY_VAR, "  abc  ")
    assert fred.api_key() == "abc"


def test_fred_parse_and_url() -> None:
    url = fred.observations_url("UNRATE", "k3y", date(1990, 1, 1))
    assert "series_id=UNRATE" in url and "observation_start=1990-01-01" in url
    obs = fred.parse_observations(fred_body([(date(2025, 9, 1), 4.4), (date(2025, 10, 1), None)]))
    assert obs == [(date(2025, 9, 1), 4.4), (date(2025, 10, 1), None)]
    with pytest.raises(ValueError, match="not a FRED"):
        fred.parse_observations(b"<html>")


def test_fhfa_parse() -> None:
    parsed = fhfa.parse_hpi("geo,year,qtr,index\nUSA,2026,1,700.5\nDV_PAC,2026,1,1\n", keep=["USA"])
    assert parsed == {"USA": {(2026, 1): 700.5}}
    with pytest.raises(ValueError, match="duplicate"):
        fhfa.parse_hpi("AA,2026,1,1\nAA,2026,1,2\n")
    with pytest.raises(ValueError, match="quarter"):
        fhfa.parse_hpi("AA,2026,5,1\n")
    with pytest.raises(ValueError, match="4 columns"):
        fhfa.parse_hpi("AA,2026,1\n")


def test_fed_parse_values_and_long_rows() -> None:
    contract = small_contract()
    header, rows = fed_scenarios.parse_csv("﻿" + fed_text("Supervisory Baseline", contract) + "\n,,\n")
    assert len(rows) == 13
    vals = fed_scenarios.values(header, rows, ["Unemployment rate"])
    assert vals[(2026, 1)] == {"Unemployment rate": 4.5}
    long = fed_scenarios.long_rows("baseline", header, rows[:1])
    assert len(long) == len(header) - 2 and long[0]["year"] == 2026
    with pytest.raises(ValueError, match="empty"):
        fed_scenarios.parse_csv("\n")


# ------------------------------------------------------------------ downloads


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_fetch_retries_then_succeeds() -> None:
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)  # type: ignore[arg-type]
        if len(calls) == 2:
            raise urllib.error.URLError("reset")
        return FakeResponse(b"ok")

    assert http.fetch("https://x/a", opener=opener, sleep=lambda s: None) == b"ok"
    assert len(calls) == 3


def test_fetch_error_never_shows_the_secret() -> None:
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)  # type: ignore[arg-type]

    with pytest.raises(http.DownloadError) as info:
        http.fetch(
            "https://x/a?api_key=SECRET123", secrets=["SECRET123"], opener=opener, sleep=lambda s: None
        )
    assert "SECRET123" not in str(info.value) and "***" in str(info.value) and "HTTP 400" in str(info.value)


# ------------------------------------------------------------------ bronze and silver


def test_full_load_and_silver(tmp_path: Path) -> None:
    contract = small_contract()
    get = fake_fetch(contract)
    fred_summary = macro_build.load_fred(
        tmp_path, RUN, key="k", fetch=get, contract=contract, pause=lambda s: None
    )
    assert fred_summary["contract"] == "pass" and fred_summary["series_checked"] == 7
    assert (macro_build.raw_dir(tmp_path, "fred", RUN) / "UNRATE.json").exists()
    assert macro_build.load_fhfa(tmp_path, RUN, fetch=get, contract=contract)["bronze_rows"] == 3 * 150
    assert (
        macro_build.load_fed(tmp_path, RUN, fetch=get, contract=contract)["bronze_rows"] == (2 * 13 + 8) * 16
    )

    result = macro_build.build_silver(tmp_path, contract)
    rows = result["silver_rows"]
    assert rows["fed_paths"] == 3 * 13 * len(macro.FED_VARIABLES)  # every Fed quarter (shifted)
    assert result["fed_timing"] == "shifted" and result["fed_start_quarter"] == "2025 Q4"
    worst = result["severely_adverse_worst"]
    assert worst["peak_unemployment_quarter"] == "2029 Q3"  # Fed's 2029 Q1, shifted two quarters
    assert rows["long_run_averages"] == 6 + 2 * 2
    assert result["anchor_methods"]["hpi"] == macro.EXTRAPOLATED
    assert result["gap_filled"] == ["2025-10"] and result["provisional_months"] == 3

    paths = pq.read_table(macro_build.silver_path(tmp_path, "fed_paths")).to_pylist()
    severe_ur = [
        p for p in paths if p["scenario"] == "severely_adverse" and p["variable"] == "unemployment_rate"
    ]
    first, second = severe_ur[0], severe_ur[1]
    assert (first["horizon"], first["year"], first["quarter"]) == (1, 2026, 3)
    assert (first["fed_year"], first["fed_quarter"]) == (2026, 1)  # Fed quarter 1 lands on 2026 Q3
    # anchor (June 2026) + (Fed 2026 Q2 - Fed start 2025 Q4) = anchor + (4.5 + 6/12 - 4.5)
    assert second["path_value"] == pytest.approx(result["anchors"]["unemployment_rate"] + 0.5)
    state = pq.read_table(macro_build.silver_path(tmp_path, "state_monthly")).to_pylist()
    assert {r["state"] for r in state} == set(STATES)
    assert min(r["month"] for r in state) == date(1990, 1, 1)


def test_contract_failure_stops_before_bronze(tmp_path: Path) -> None:
    contract = small_contract()
    broken = fred_body(monthly_obs(gap=date(2024, 3, 1), level=4.0))
    get = fake_fetch(contract, {"series_id=AAUR": broken})
    with pytest.raises(ContractViolation, match="AAUR"):
        macro_build.load_fred(tmp_path, RUN, key="k", fetch=get, contract=contract, pause=lambda s: None)
    assert not macro_build.bronze_path(tmp_path, "fred").exists()
    assert (macro_build.raw_dir(tmp_path, "fred", RUN) / "AAUR.json").exists()  # kept for diagnosis


def test_write_table_is_atomic(tmp_path: Path) -> None:
    import pyarrow as pa

    path = tmp_path / "x" / "t.parquet"
    assert macro_build.write_table(pa.table({"a": [1, 2]}), path) == 2
    assert not path.with_suffix(".parquet.tmp").exists()
    with pytest.raises(ValueError, match="empty silver"):
        macro_build._table([])
