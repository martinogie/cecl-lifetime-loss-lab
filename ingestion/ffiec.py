"""FFIEC call reports (Schedule RI-C) for the Phase 6 peer comparison: download, check, aggregate.

Martins's decisions (20 November 2026): the official FFIEC CDR Public Web Service (REST API, SIS611
v1.10), not manual downloads; CECL's own pipeline (Bank Health is never touched); aggregates only.

How it works, per reporting period:
1. `RetrievePanelOfReporters`: every institution expected to file for the period.
2. RI-C filers: institutions with $1 billion or more in total assets on 30 June of the previous year
   (the RI-C instructions), taken from the FDIC's public API and matched on the FDIC certificate number.
3. `RetrieveFacsimile` (SDF) for each filer. Only a few numbers per bank are kept, by item code: the RI-C
   residential real estate allowance and amortized cost (item 1.c), the RI-C total allowance (item 6), the
   Schedule RC allowance (item 4.c, code 3123), total assets (code 2170), and which RI-C layout (CECL or
   the older incurred-loss one) the bank filed. Each bank-period is cached as a small
   JSON file, so a re-run skips everything already done (resumable, idempotent).
4. The data contract (`contracts/ffiec_call.yml`) checks every bank-period (types, signs, and RI-C item 6
   = RC item 4.c) and every period (share of filers without a usable RI-C block); a failure stops the load.
5. Aggregates: the residential allowance ratio by bank-size group (median, middle 50%, 10th-90th
   percentile, and the pooled ratio). Only these leave the machine.

Note on the measure: RI-C item 1.c is "residential real estate loans" as each bank segments its
allowance (1-4 family first and junior liens, home-equity lines; some banks include multifamily). It is
the closest public measure to a 1-4 family mortgage reserve ratio; the memo says so.

Secrets: FFIEC_PWS_USERNAME and FFIEC_PWS_TOKEN come from the environment (.env, added by Martins). They
travel only in request headers, are never printed or logged, and every error message is redacted. The
token's expiry date is read from the token itself (its standard "exp" field) so the pipeline can warn
14 days ahead and stop with a clear message once it has expired.
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ingestion import http

CONTRACT_PATH = Path(__file__).with_name("contracts") / "ffiec_call.yml"
USER_VAR, TOKEN_VAR = "FFIEC_PWS_USERNAME", "FFIEC_PWS_TOKEN"
CREATED_VAR = "FFIEC_PWS_TOKEN_CREATED"  # optional, a date (not secret): used only if the token has no "exp"
USER_AGENT = "cecl-call-report-loader/1.0"  # generic client name: no personal details to outside services
SDF_COLUMNS = ("call_date", "rssd", "mdrm", "value", "last_update", "definition", "schedule", "line")


class FfiecError(RuntimeError):
    """The FFIEC load failed. The message never contains a secret."""


class TokenExpired(FfiecError):
    """The PWS token has expired: Martins must generate a new one in his PWS account."""


class ContractError(FfiecError):
    """The data broke its contract: the load stops before anything is written."""


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------------------- credentials


@dataclass(frozen=True)
class Credentials:
    user: str
    token: str

    def __repr__(self) -> str:  # never show the values, even in a traceback
        return "Credentials(user=***, token=***)"

    @property
    def secrets(self) -> tuple[str, str]:
        return (self.user, self.token)


def load_env_file(
    path: Path = Path(".env"), keys: tuple[str, ...] = (USER_VAR, TOKEN_VAR, CREATED_VAR)
) -> None:
    """On the PC: copy only the FFIEC keys from .env into this process's environment (values never shown).
    A key already set in the environment wins; a missing file is fine (Airflow sets them itself)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = raw.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if sep and key in keys and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def credentials(env: Mapping[str, str] | None = None) -> Credentials:
    e = env if env is not None else os.environ
    user, token = e.get(USER_VAR, "").strip(), e.get(TOKEN_VAR, "").strip()
    missing = [v for v, x in ((USER_VAR, user), (TOKEN_VAR, token)) if not x or x.startswith("<")]
    if missing:
        raise FfiecError(f"{' and '.join(missing)} not set: Martins adds them to .env (never in git)")
    return Credentials(user, token.removeprefix("Bearer ").strip())


def token_expiry(token: str, env: Mapping[str, str] | None = None) -> date | None:
    """The token's expiry date: its standard JWT "exp" field, else FFIEC_PWS_TOKEN_CREATED + 90 days."""
    parts = token.split(".")
    if len(parts) == 3:
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            if "exp" in payload:
                return datetime.fromtimestamp(int(payload["exp"]), tz=UTC).date()
        except (ValueError, TypeError):
            pass
    created = (env if env is not None else os.environ).get(CREATED_VAR, "").strip()
    if created:
        return date.fromisoformat(created) + timedelta(days=int(load_contract()["token_lifetime_days"]))
    return None


def token_status(today: date, expiry: date | None, warning_days: int = 14) -> tuple[str, int | None]:
    """("ok" | "expiring" | "expired" | "unknown", days left)."""
    if expiry is None:
        return "unknown", None
    left = (expiry - today).days
    if left < 0:
        return "expired", left
    return ("expiring" if left <= warning_days else "ok"), left


def check_token(creds: Credentials, today: date, warning_days: int = 14) -> tuple[str, int | None]:
    """Stop with a clear message if the token has expired; otherwise return its status."""
    status, left = token_status(today, token_expiry(creds.token), warning_days)
    if status == "expired":
        raise TokenExpired(
            f"The FFIEC PWS token expired {-(left or 0)} day(s) ago (lifetime 90 days). Martins: log in to "
            "the PWS account, generate a new token and update FFIEC_PWS_TOKEN in .env."
        )
    return status, left


def token_warning(today: date, env: Mapping[str, str] | None = None) -> str | None:
    """For the weekly health check: a warning line if the token expires within 14 days or has expired
    (sent to the [CECL] channels), else None. Nothing is said before the credentials are set up."""
    try:
        creds = credentials(env)
    except FfiecError:
        return None
    days = int(load_contract()["token_warning_days"])
    expiry = token_expiry(creds.token, env)
    status, left = token_status(today, expiry, days)
    if status == "expired":
        return (f"FFIEC PWS token EXPIRED on {expiry}: call-report loads will fail. Generate a new token in the "
                "PWS account and update FFIEC_PWS_TOKEN in .env")  # fmt: skip
    if status == "expiring":
        return (f"FFIEC PWS token expires in {left} day(s), on {expiry}: generate a new token in the PWS account "
                "and update FFIEC_PWS_TOKEN in .env")  # fmt: skip
    if status == "unknown":
        return f"FFIEC PWS token expiry unknown: set {CREATED_VAR} (the date the token was made) in .env"
    return None


# --------------------------------------------------------------------------------------------- client


Opener = Callable[..., Any]


class Client:
    """Polite REST client: one call at a time, at least `min_interval` seconds apart, with retries and
    exponential back-off on rate limits, quota and server errors."""

    def __init__(self, creds: Credentials, contract: Mapping[str, Any] | None = None,
                 opener: Opener = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        c = contract or load_contract()
        self._creds, self._opener, self._sleep, self._clock = creds, opener, sleep, clock
        self.base = str(c["base_url"])
        self.series = str(c["data_series"])
        self.min_interval = float(c["min_seconds_between_calls"])
        self.retries, self.backoff = int(c["retries"]), float(c["backoff_seconds"])
        self._last = -1e18
        self.calls = 0

    def _clean(self, text: str) -> str:
        return http.redact(text, self._creds.secrets)

    def call(self, function: str, headers: Mapping[str, str]) -> bytes | None:
        """GET one API function. None for "no content" (204: facsimile not found)."""
        h = {"UserID": self._creds.user, "Authentication": f"Bearer {self._creds.token}",
             "dataSeries": self.series, "User-Agent": USER_AGENT, "Accept": "application/json", **headers}  # fmt: skip
        last = ""
        for attempt in range(1, self.retries + 1):
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            self.calls += 1
            try:
                with self._opener(urllib.request.Request(self.base + function, headers=h), timeout=120) as r:
                    if getattr(r, "status", 200) == 204:
                        return None
                    return bytes(r.read())
            except urllib.error.HTTPError as e:
                body = self._clean(e.read().decode("utf-8", "replace")[:300]) if e.fp else ""
                if e.code == 204:
                    return None
                if e.code == 400 and "expired" in body.lower():
                    raise TokenExpired("The FFIEC PWS token has expired (the API says so). Martins: generate a "
                                       "new token in the PWS account and update FFIEC_PWS_TOKEN in .env.") from None  # fmt: skip
                if e.code in (400, 401) and "token" in body.lower():
                    raise FfiecError(f"FFIEC rejected the credentials (HTTP {e.code}): check FFIEC_PWS_USERNAME "
                                     "and FFIEC_PWS_TOKEN in .env") from None  # fmt: skip
                if e.code == 403 and "quota" in body.lower():
                    last = "call quota used up"
                    self._sleep(max(self.backoff * 2**attempt, 300.0))  # the quota refills over the hour
                    continue
                if e.code in (429, 500, 502, 503, 504):
                    last = f"HTTP {e.code}"
                    self._sleep(self.backoff * 2 ** (attempt - 1))
                    continue
                raise FfiecError(f"{function}: HTTP {e.code} {body}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = type(e).__name__
                self._sleep(self.backoff * 2 ** (attempt - 1))
        raise FfiecError(f"{function}: failed after {self.retries} attempts ({last})")

    def panel(self, period: date) -> list[dict[str, Any]]:
        body = self.call("RetrievePanelOfReporters", {"reportingPeriodEndDate": period.strftime("%m/%d/%Y")})
        return list(json.loads(body or b"[]"))

    def facsimile(self, period: date, rssd: int) -> str | None:
        body = self.call("RetrieveFacsimile", {"reportingPeriodEndDate": period.strftime("%m/%d/%Y"),
                                               "fiIdType": "ID_RSSD", "fiId": str(rssd), "facsimileFormat": "SDF"})  # fmt: skip
        return None if body is None else decode_facsimile(body)


def decode_facsimile(body: bytes) -> str:
    """The facsimile comes as a JSON byte stream: a base64 string, a list of byte values, or raw text."""
    try:
        data = json.loads(body)
    except ValueError:
        return body.decode("utf-8-sig", "replace")
    if isinstance(data, dict):
        data = data.get("FacsimileFile", data)
    if isinstance(data, list) and len(data) == 1 and not isinstance(data[0], int):
        data = data[0]
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True).decode("utf-8-sig", "replace")
        except ValueError:
            return data
    if isinstance(data, list):
        return bytes(data).decode("utf-8-sig", "replace")
    raise FfiecError("unexpected facsimile format")


# ------------------------------------------------------------------------------------------ extraction


def parse_sdf(text: str) -> list[dict[str, str]]:
    """SDF lines: call date; RSSD; MDRM; value; last update; short definition; schedule; line number.
    A header line (any line naming "MDRM") is skipped."""
    rows = []
    for raw in text.splitlines():
        parts = [p.strip().strip('"') for p in raw.split(";")]
        if len(parts) < len(SDF_COLUMNS) or "MDRM" in parts[2].upper():
            continue
        rows.append(dict(zip(SDF_COLUMNS, parts[: len(SDF_COLUMNS)], strict=True)))
    return rows


def _number(text: str) -> float | None:
    t = text.replace(",", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def codes(rows: Iterable[Mapping[str, str]]) -> dict[str, float]:
    """Item values by code without its prefix ("RCFDJJ14" -> "JJ14"); RCFD wins over RCON."""
    out: dict[str, tuple[int, float]] = {}
    rank = {"RCFD": 0, "RCON": 1}
    for r in rows:
        mdrm = r["mdrm"].strip().upper()
        prefix, item = mdrm[:4], mdrm[4:]
        v = _number(r["value"])
        if v is None or prefix not in rank:
            continue
        if item not in out or rank[prefix] < out[item][0]:
            out[item] = (rank[prefix], v)
    return {k: v for k, (_, v) in out.items()}


def extract(text: str, contract: Mapping[str, Any]) -> dict[str, Any]:
    """The kept items of one bank's report, or a status saying why there are none.

    Two RI-C layouts (CECL and the older incurred-loss one) are named by item code in the contract; the
    layout whose total allowance ties to Schedule RC item 4.c is used, and recorded as `method`."""
    found = codes(parse_sdf(text))
    out: dict[str, Any] = {"rc_total_allowance": found.get(contract["rc_total_allowance"]),
                           "total_assets": found.get(contract["total_assets"])}  # fmt: skip
    tol = float(contract["tie_out_tolerance_thousands"])
    candidates = []
    for method, items in contract["layouts"].items():
        if not all(any(c in found for c in items[k]) for k in items):
            continue
        rec = {k: float(sum(found.get(c, 0.0) for c in items[k])) for k in items}
        ties = (
            out["rc_total_allowance"] is not None
            and abs(rec["ric_total_allowance"] - out["rc_total_allowance"]) <= tol
        )
        candidates.append((not ties, method, rec))
    if not candidates:
        return out | {"status": "no_ric"}
    _, method, rec = sorted(candidates, key=lambda c: (c[0], c[1]))[0]
    out |= rec | {"method": method}
    return out | {"status": check_bank(out, contract)}


def check_bank(rec: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    """Contract on one bank-period: required numbers present and not negative; RI-C total = RC 4.c."""
    for key in contract["required_items"]:
        v = rec.get(key)
        if v is None or not isinstance(v, int | float) or v < 0:
            return f"contract_failed: {key}"
    if abs(rec["ric_total_allowance"] - rec["rc_total_allowance"]) > float(
        contract["tie_out_tolerance_thousands"]
    ):
        return "contract_failed: RI-C item 6 allowance does not equal Schedule RC item 4.c"
    return "ok"


# ------------------------------------------------------------------------------------------- the load


def prior_june(period: date) -> date:
    return date(period.year - 1, 6, 30)


def fdic_assets(
    repdte: date, fetch: http.Fetch | None = None, contract: Mapping[str, Any] | None = None
) -> dict[int, float]:
    """Total assets (thousands) by FDIC certificate number at `repdte`, from the FDIC's public API."""
    c = contract or load_contract()
    get = fetch or http.fetch
    out: dict[int, float] = {}
    offset = 0
    while True:
        url = (f"{c['fdic_api']}?filters=REPDTE:{repdte:%Y%m%d}&fields=CERT,ASSET&limit=10000&offset={offset}"
               "&format=json")  # fmt: skip
        data = json.loads(get(url))
        rows = [d["data"] for d in data.get("data", [])]
        for r in rows:
            if r.get("CERT") is not None and r.get("ASSET") is not None:
                out[int(r["CERT"])] = float(r["ASSET"])
        offset += len(rows)
        if not rows or offset >= int(data.get("meta", {}).get("total", 0)):
            return out


def ric_filers(
    panel: Iterable[Mapping[str, Any]], assets: Mapping[int, float], threshold: float
) -> list[int]:
    """RSSD IDs of institutions that filed for the period and had at least `threshold` total assets on
    30 June of the previous year."""
    return sorted(int(p["ID_RSSD"]) for p in panel
                  if p.get("HasFiledForReportingPeriod") and assets.get(int(p.get("FDICCertNumber") or 0), 0.0) >= threshold)  # fmt: skip


def cache_path(root: Path, period: date, rssd: int) -> Path:
    return Path(root) / "raw" / "ffiec" / f"{period:%Y%m%d}" / f"{rssd}.json"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_period(root: Path, period: date, client: Client, contract: Mapping[str, Any],
                fetch: http.Fetch | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:  # fmt: skip
    """Download and check one period. Cached bank-periods are skipped. Raises ContractError if the period
    breaks the contract; nothing is written to bronze then."""
    panel = client.panel(period)
    assets = fdic_assets(prior_june(period), fetch, contract)
    filers = ric_filers(panel, assets, float(contract["ric_asset_threshold_thousands"]))
    counts = {"panel": len(panel), "ric_filers": len(filers), "cached": 0, "fetched": 0}
    for i, rssd in enumerate(filers, 1):
        path = cache_path(root, period, rssd)
        if path.exists():
            counts["cached"] += 1
            continue
        text = client.facsimile(period, rssd)
        rec = {"status": "not_found"} if text is None else extract(text, contract)
        rec |= {"rssd": rssd, "period": period.isoformat()}
        _write_atomic(path, json.dumps(rec))
        counts["fetched"] += 1
        if i % 100 == 0:
            log(f"{period}: {i}/{len(filers)} filers")
    recs = [json.loads(cache_path(root, period, r).read_text(encoding="utf-8")) for r in filers]
    status = [str(r["status"]) for r in recs]
    counts |= {"ok": status.count("ok"), "no_ric": sum(s in ("no_ric", "not_found") for s in status),
               "contract_failed": sum(s.startswith("contract_failed") for s in status)}  # fmt: skip
    enforce_period(period, counts, contract)
    return counts


def enforce_period(period: date, counts: Mapping[str, int], contract: Mapping[str, Any]) -> None:
    n = max(counts["ric_filers"], 1)
    problems = []
    if counts["ric_filers"] == 0:
        problems.append("no RI-C filers found")
    if counts["no_ric"] / n > float(contract["max_share_without_ric"]):
        problems.append(f"{counts['no_ric']} of {n} filers without a usable RI-C block")
    if counts["contract_failed"] / n > float(contract["max_share_contract_failed"]):
        problems.append(f"{counts['contract_failed']} of {n} filers failed the bank-level contract")
    if problems:
        raise ContractError(f"FFIEC {period}: " + "; ".join(problems))


def bronze_rows(root: Path, periods: Iterable[date]) -> list[dict[str, Any]]:
    """Every usable bank-period from the cache (kept on the PC / VM only; never published)."""
    rows = []
    for p in periods:
        folder = Path(root) / "raw" / "ffiec" / f"{p:%Y%m%d}"
        for f in sorted(folder.glob("*.json")) if folder.exists() else []:
            r = json.loads(f.read_text(encoding="utf-8"))
            if r.get("status") == "ok":
                rows.append(r)
    return rows


def size_group(assets: float, contract: Mapping[str, Any]) -> str | None:
    for name, (lo, hi) in contract["size_groups_thousands"].items():
        if assets >= lo and (hi is None or assets < hi):
            return str(name)
    return None


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def aggregates(rows: Iterable[Mapping[str, Any]], contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The residential allowance ratio by period and bank-size group (and all banks): the only output that
    is published. Banks with less than the minimum residential loans are left out (counted)."""
    floor = float(contract["min_residential_loans_thousands"])
    groups: dict[tuple[str, str], list[tuple[float, float]]] = {}
    left_out: dict[str, int] = {}
    methods: dict[str, dict[str, int]] = {}
    for r in rows:
        if r["residential_loans"] < floor:
            left_out[r["period"]] = left_out.get(r["period"], 0) + 1
            continue
        methods.setdefault(r["period"], {}).setdefault(str(r.get("method", "unknown")), 0)
        methods[r["period"]][str(r.get("method", "unknown"))] += 1
        g = size_group(r["total_assets"], contract)
        for key in ((r["period"], g or "other"), (r["period"], "All banks $1bn+")):
            groups.setdefault(key, []).append((r["residential_allowance"], r["residential_loans"]))
    out = []
    for (period, group), pairs in sorted(groups.items()):
        ratios = [a / loans for a, loans in pairs]
        out.append({"period": period, "size_group": group, "banks": len(pairs),
                    "median": statistics.median(ratios), "p25": _quantile(ratios, 0.25), "p75": _quantile(ratios, 0.75),
                    "p10": _quantile(ratios, 0.10), "p90": _quantile(ratios, 0.90),
                    "pooled": sum(a for a, _ in pairs) / sum(loans for _, loans in pairs),
                    "left_out_small_residential_books": left_out.get(period, 0) if group == "All banks $1bn+" else None,
                    "banks_by_method": methods.get(period, {}) if group == "All banks $1bn+" else None})  # fmt: skip
    return out


def periods_from(contract: Mapping[str, Any], extra: Iterable[date] = ()) -> list[date]:
    return sorted({date.fromisoformat(p) for p in contract["initial_periods"]} | set(extra))


def latest_quarter_end(today: date) -> date:
    """The most recent quarter-end whose call reports should be public (filed 30-35 days after the
    quarter, published within about 45 days): a quarter counts once 50 days have passed."""
    q_ends = [
        date(y, m, d) for y in (today.year - 1, today.year) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))
    ]
    return max(q for q in q_ends if (today - q).days >= 50)


def run(root: Path, periods: Iterable[date], today: date, env: Mapping[str, str] | None = None,
        opener: Opener = urllib.request.urlopen, fetch: http.Fetch | None = None,
        sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> dict[str, Any]:  # fmt: skip
    """The whole load: token check, each period (resumable), contract, aggregates."""
    contract = load_contract()
    creds = credentials(env)
    status, left = check_token(creds, today, int(contract["token_warning_days"]))
    client = Client(creds, contract, opener=opener, sleep=sleep)
    plist = list(periods)
    summary: dict[str, Any] = {"token": {"status": status, "days_left": left}, "periods": {}}
    for p in plist:
        summary["periods"][p.isoformat()] = load_period(root, p, client, contract, fetch, log)
    rows = bronze_rows(root, plist)
    summary["aggregates"] = aggregates(rows, contract)
    summary["bank_periods_ok"] = len(rows)
    summary["api_calls"] = client.calls
    return summary
