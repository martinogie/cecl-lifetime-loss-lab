"""FFIEC call-report loader: made-up fixtures only, no network (Phase 6 peer comparison)."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ingestion import ffiec

CONTRACT = ffiec.load_contract()
ENV = {ffiec.USER_VAR: "made-up-user", ffiec.TOKEN_VAR: "made-up-token"}


def jwt(exp: date) -> str:
    """A made-up token with only an expiry (not signed; the loader only reads "exp")."""
    from datetime import UTC, datetime

    payload = {"exp": int(datetime(exp.year, exp.month, exp.day, tzinfo=UTC).timestamp())}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def sdf(res_allow: float = 120, res_loans: float = 60000, total: float = 900, rc: float = 900,
        assets: float = 5_000_000, incurred: bool = False, prefix: str = "RCON") -> str:  # fmt: skip
    """A made-up report in the real SDF layout (verified on live reports, 20 November 2026)."""
    lines = [
        "Call Date;Bank RSSD Identifier;MDRM #;Value;Last Update;Short Definition;Call Schedule;Line Number"
    ]
    if incurred:  # older RI-C Part I: three columns per item, by impairment method
        rows = [("M721", res_loans * 0.1, "RICI", "1c"), ("M722", res_allow * 0.5, "RICI", "1c"),
                ("M723", res_loans * 0.9, "RICI", "1c"), ("M724", res_allow * 0.5, "RICI", "1c"),
                ("M725", 0, "RICI", "1c"), ("M726", 0, "RICI", "1c"),
                ("M747", total / 2, "RICI", "6"), ("M749", total / 2, "RICI", "6"), ("M751", 0, "RICI", "6")]  # fmt: skip
    else:
        rows = [("JJ06", res_loans, "RIC", "1c"), ("JJ14", res_allow, "RIC", "1c"), ("JJ11", 9e5, "RIC", "6"),
                ("JJ19", total, "RIC", "6")]  # fmt: skip
    rows += [("3123", rc, "RCRII", "6"), ("2170", assets, "RCRII", "11")]
    for code, v, sched, ln in rows:
        lines.append(f"20251231;111;{prefix}{code};{v};20260130;Residential real estate loans;{sched};{ln}")
    lines.append("20251231;111;RCOA7204;10.35%;20260130;Leverage ratio;RCRI;31")  # not a number: ignored
    return "\n".join(lines)


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *a: object) -> None:
        pass


class FakeApi:
    """Stands in for the FFIEC API: answers by function and fiId; can fail a number of times first."""

    def __init__(self, panel: list[dict[str, Any]], reports: dict[int, str | None], fail_first: int = 0,
                 fail_code: int = 429, fail_body: str = "slow down") -> None:  # fmt: skip
        self.panel, self.reports, self.fail_first = panel, reports, fail_first
        self.fail_code, self.fail_body = fail_code, fail_body
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> FakeResponse:
        self.requests.append(request)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise urllib.error.HTTPError(
                request.full_url, self.fail_code, "x", {}, io.BytesIO(self.fail_body.encode())
            )  # type: ignore[arg-type]
        function = request.full_url.rsplit("/", 1)[-1]
        if function == "RetrievePanelOfReporters":
            return FakeResponse(json.dumps(self.panel).encode())
        rssd = int(request.get_header("Fiid"))
        text = self.reports.get(rssd)
        if text is None:
            r = FakeResponse(b"")
            r.status = 204
            return r
        return FakeResponse(json.dumps(base64.b64encode(text.encode()).decode()).encode())


def fdic_fetch(assets: dict[int, float]) -> Any:
    def fetch(url: str) -> bytes:
        rows = [{"data": {"CERT": c, "ASSET": a}} for c, a in assets.items()]
        return json.dumps({"meta": {"total": len(rows)}, "data": rows if "offset=0" in url else []}).encode()

    return fetch


PANEL = [
    {"ID_RSSD": 111, "FDICCertNumber": 1, "HasFiledForReportingPeriod": True},
    {"ID_RSSD": 222, "FDICCertNumber": 2, "HasFiledForReportingPeriod": True},
    {
        "ID_RSSD": 333,
        "FDICCertNumber": 3,
        "HasFiledForReportingPeriod": True,
    },  # under $1bn: not an RI-C filer
    {"ID_RSSD": 444, "FDICCertNumber": 4, "HasFiledForReportingPeriod": False},  # did not file
]
ASSETS = {1: 2_000_000, 2: 150_000_000, 3: 500_000, 4: 3_000_000}


def test_credentials_are_required_and_never_shown() -> None:
    with pytest.raises(ffiec.FfiecError, match="FFIEC_PWS_USERNAME and FFIEC_PWS_TOKEN not set"):
        ffiec.credentials({})
    c = ffiec.credentials({**ENV, ffiec.TOKEN_VAR: "Bearer made-up-token"})
    assert c.token == "made-up-token" and "made-up" not in repr(c)


def test_token_expiry_and_warnings() -> None:
    today = date(2026, 11, 20)
    assert ffiec.token_expiry(jwt(date(2027, 1, 1))) == date(2027, 1, 1)
    assert ffiec.token_status(today, date(2027, 1, 1)) == ("ok", 42)
    assert ffiec.token_status(today, date(2026, 12, 1))[0] == "expiring"
    assert ffiec.token_status(today, date(2026, 11, 1))[0] == "expired"
    assert ffiec.token_expiry("not-a-jwt", {ffiec.CREATED_VAR: "2026-11-01"}) == date(2027, 1, 30)
    with pytest.raises(ffiec.TokenExpired, match="generate a new token"):
        ffiec.check_token(ffiec.Credentials("u", jwt(date(2026, 11, 1))), today)
    env = {**ENV, ffiec.TOKEN_VAR: jwt(date(2026, 12, 1))}
    assert "expires in 11 day" in str(ffiec.token_warning(today, env))
    assert "EXPIRED" in str(ffiec.token_warning(today, {**ENV, ffiec.TOKEN_VAR: jwt(date(2026, 11, 1))}))
    assert ffiec.token_warning(today, {**ENV, ffiec.TOKEN_VAR: jwt(date(2027, 6, 1))}) is None
    assert ffiec.token_warning(today, {}) is None  # not set up yet: nothing to say


def test_extract_both_layouts_by_item_code() -> None:
    rec = ffiec.extract(sdf(), CONTRACT)
    assert rec["status"] == "ok" and rec["method"] == "cecl"
    assert rec["residential_allowance"] == 120 and rec["residential_loans"] == 60000
    assert rec["total_assets"] == 5_000_000 and rec["rc_total_allowance"] == 900
    old = ffiec.extract(sdf(incurred=True), CONTRACT)
    assert old["status"] == "ok" and old["method"] == "incurred"
    assert old["residential_allowance"] == pytest.approx(120) and old["residential_loans"] == pytest.approx(
        60000
    )
    assert ffiec.extract(sdf(rc=800), CONTRACT)["status"].startswith("contract_failed")  # item 6 != RC 4.c
    no_ric = "\n".join(line for line in sdf().splitlines() if ";RIC;" not in line)
    assert ffiec.extract(no_ric, CONTRACT)["status"] == "no_ric"
    both = sdf(prefix="RCFD") + "\n" + "\n".join(sdf(res_allow=1).splitlines()[1:])  # RCFD wins over RCON
    assert ffiec.extract(both, CONTRACT)["residential_allowance"] == 120


def test_decode_facsimile_formats() -> None:
    text = "a;b"
    assert ffiec.decode_facsimile(json.dumps(base64.b64encode(text.encode()).decode()).encode()) == text
    assert ffiec.decode_facsimile(json.dumps(list(text.encode())).encode()) == text
    assert ffiec.decode_facsimile(json.dumps({"FacsimileFile": list(text.encode())}).encode()) == text
    assert ffiec.decode_facsimile(text.encode()) == text


def test_client_throttles_retries_and_hides_secrets() -> None:
    api = FakeApi(PANEL, {}, fail_first=2)
    slept: list[float] = []
    clock = iter(range(0, 10_000)).__next__
    client = ffiec.Client(
        ffiec.credentials(ENV), CONTRACT, opener=api, sleep=slept.append, clock=lambda: float(clock())
    )
    assert len(client.panel(date(2025, 12, 31))) == 4
    assert len(api.requests) == 3 and any(s >= CONTRACT["backoff_seconds"] for s in slept)  # two back-offs
    h = {k.lower(): v for k, v in api.requests[-1].header_items()}
    assert h["authentication"] == "Bearer made-up-token" and h["reportingperiodenddate"] == "12/31/2025"
    leaky = FakeApi(PANEL, {}, fail_first=9, fail_code=418, fail_body="echo made-up-token")
    with pytest.raises(ffiec.FfiecError) as err:
        ffiec.Client(ffiec.credentials(ENV), CONTRACT, opener=leaky, sleep=lambda s: None).panel(
            date(2025, 12, 31)
        )
    assert "made-up-token" not in str(err.value) and "***" in str(err.value)
    expired = FakeApi(
        PANEL, {}, fail_first=1, fail_code=400, fail_body='{"Message": "Security Token is expired."}'
    )
    with pytest.raises(ffiec.TokenExpired):
        ffiec.Client(ffiec.credentials(ENV), CONTRACT, opener=expired, sleep=lambda s: None).panel(
            date(2025, 12, 31)
        )


def test_load_is_resumable_and_checks_the_contract(tmp_path: Path) -> None:
    period = date(2025, 12, 31)
    api = FakeApi(
        PANEL,
        {111: sdf(), 222: sdf(res_allow=300, res_loans=100000, total=5000, rc=5000, assets=150_000_000)},
    )
    client = ffiec.Client(ffiec.credentials(ENV), CONTRACT, opener=api, sleep=lambda s: None)
    counts = ffiec.load_period(
        tmp_path, period, client, CONTRACT, fetch=fdic_fetch(ASSETS), log=lambda m: None
    )
    assert (
        counts["ric_filers"] == 2 and counts["fetched"] == 2 and counts["ok"] == 2
    )  # 333 too small, 444 not filed
    calls = len(api.requests)
    again = ffiec.load_period(
        tmp_path, period, client, CONTRACT, fetch=fdic_fetch(ASSETS), log=lambda m: None
    )
    assert (
        again["cached"] == 2 and again["fetched"] == 0 and len(api.requests) == calls + 1
    )  # only the panel call
    bad = FakeApi(PANEL, {111: None, 222: None})
    with pytest.raises(ffiec.ContractError, match="without a usable RI-C block"):
        ffiec.load_period(tmp_path / "b", period, ffiec.Client(ffiec.credentials(ENV), CONTRACT, opener=bad,
                          sleep=lambda s: None), CONTRACT, fetch=fdic_fetch(ASSETS), log=lambda m: None)  # fmt: skip


def test_covid_relief_uses_the_lesser_assets(tmp_path: Path) -> None:
    """2021 reports: the $1bn test on the lesser of June 2020 and December 2019 assets."""
    period = date(2021, 12, 31)

    def fetch(url: str) -> bytes:
        assets = {1: 2_000_000, 2: 150_000_000} if "20200630" in url else {1: 900_000, 2: 150_000_000}
        rows = [{"data": {"CERT": c, "ASSET": a}} for c, a in assets.items()]
        return json.dumps({"meta": {"total": len(rows)}, "data": rows if "offset=0" in url else []}).encode()

    api = FakeApi(PANEL, {222: sdf()})
    client = ffiec.Client(ffiec.credentials(ENV), CONTRACT, opener=api, sleep=lambda s: None)
    counts = ffiec.load_period(tmp_path, period, client, CONTRACT, fetch=fetch, log=lambda m: None)
    assert counts["ric_filers"] == 1 and counts["ok"] == 1  # bank 111 was under $1bn at 31 Dec 2019


def test_aggregates_by_size_group() -> None:
    rows = [
        {
            "period": "2025-12-31",
            "residential_allowance": 2.0,
            "residential_loans": 1000.0,
            "total_assets": 2e6,
            "method": "incurred",
        },
        {
            "period": "2025-12-31",
            "residential_allowance": 4.0,
            "residential_loans": 1000.0,
            "total_assets": 3e6,
        },
        {
            "period": "2025-12-31",
            "residential_allowance": 30.0,
            "residential_loans": 10000.0,
            "total_assets": 2e8,
        },
        {
            "period": "2025-12-31",
            "residential_allowance": 1.0,
            "residential_loans": 10.0,
            "total_assets": 2e6,
        },  # too small
    ]
    out = {a["size_group"]: a for a in ffiec.aggregates(rows, CONTRACT)}
    assert out["$1-10bn"]["banks"] == 2 and out["$1-10bn"]["median"] == pytest.approx(0.003)
    assert out["$100bn+"]["pooled"] == pytest.approx(0.003)
    assert (
        out["All banks $1bn+"]["banks"] == 3
        and out["All banks $1bn+"]["left_out_small_residential_books"] == 1
    )
    assert out["All banks $1bn+"]["pooled"] == pytest.approx(36 / 12000)
    assert out["All banks $1bn+"]["banks_by_method"] == {"incurred": 1, "unknown": 2}


def test_periods_and_filers() -> None:
    assert ffiec.latest_quarter_end(date(2026, 11, 20)) == date(2026, 9, 30)
    assert ffiec.latest_quarter_end(date(2026, 11, 10)) == date(2026, 6, 30)
    assert ffiec.prior_june(date(2025, 12, 31)) == date(2024, 6, 30)
    assert ffiec.ric_filers(PANEL, ASSETS, 1_000_000) == [111, 222]
    small_form = [{**PANEL[0], "FilingType": "051"}, {**PANEL[1], "FilingType": 41}]
    assert ffiec.ric_filers(small_form, ASSETS, 1_000_000, ["051"]) == [222]  # 051 skipped in Mar/Sep
    assert ffiec.ric_filers(small_form, ASSETS, 1_000_000) == [111, 222]
    assert date(2026, 6, 30) in ffiec.periods_from(CONTRACT)


def test_env_file_loads_only_ffiec_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / ".env"
    f.write_text("FFIEC_PWS_USERNAME=made-up\nOTHER_SECRET=nope\n", encoding="utf-8")
    monkeypatch.delenv("FFIEC_PWS_USERNAME", raising=False)
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    ffiec.load_env_file(f)
    import os

    assert os.environ["FFIEC_PWS_USERNAME"] == "made-up" and "OTHER_SECRET" not in os.environ
