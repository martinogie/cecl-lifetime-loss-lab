"""FRED (St. Louis Fed): national and state economic series through the FRED API.

The key comes from the FRED_API_KEY environment variable only (the dev and prod `.env`, passed to
the Airflow containers by Compose). It is never written to a file or a log: URLs that carry it are
redacted in every error (`ingestion/http.py`), and the raw file saved is the API's JSON body, which
doesn't contain the key.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import date

from ingestion import http

API_URL = "https://api.stlouisfed.org/fred/series/observations"
KEY_VAR = "FRED_API_KEY"
MISSING = "."  # FRED's marker for "no value this period"


class MissingSecret(RuntimeError):
    """A required secret isn't set. The message names it and where it goes, never a value."""


def api_key() -> str:
    key = os.environ.get(KEY_VAR, "").strip()
    if not key:
        raise MissingSecret(
            f"{KEY_VAR} is not set. Martins adds it to the .env file (dev: the repo's .env; prod: "
            "/srv/cecl/secrets/.env); Compose passes it to the Airflow containers."
        )
    return key


def observations_url(series_id: str, key: str, start: date) -> str:
    query = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start.isoformat(),
    }
    return f"{API_URL}?{urllib.parse.urlencode(query)}"


def parse_observations(body: bytes) -> list[tuple[date, float | None]]:
    """FRED's JSON -> [(date, value or None)], in date order. Raises ValueError on a bad body."""
    try:
        payload = json.loads(body)
        observations = payload["observations"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"not a FRED observations response: {type(e).__name__}") from e
    out: list[tuple[date, float | None]] = []
    for o in observations:
        raw = str(o["value"]).strip()
        out.append((date.fromisoformat(o["date"]), None if raw in (MISSING, "") else float(raw)))
    return out


def fetch_series(
    series_id: str, key: str, start: date, fetch: http.Fetch | None = None
) -> tuple[bytes, list[tuple[date, float | None]]]:
    """Download one series: the raw body (saved as-is) and the parsed observations."""
    url = observations_url(series_id, key, start)
    body = fetch(url) if fetch else http.fetch(url, secrets=(key,))
    return body, parse_observations(body)
