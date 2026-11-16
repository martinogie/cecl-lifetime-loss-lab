"""Downloads for the public economic data: retries, a timeout, and no secrets in any message.

Standard library only (no extra package in the Airflow image). The FRED key travels in the URL's
query string, so every error message passes through `redact` first: a failed download can never
print the key into a log.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from typing import Protocol

# A generic client name: no personal or project details go to outside services.
USER_AGENT = "cecl-macro-loader/1.0"
RETRY_STATUS = (429, 500, 502, 503, 504)


class DownloadError(RuntimeError):
    """A download failed after its retries. The message never contains a secret."""


class _Response(Protocol):
    def read(self) -> bytes: ...
    def __enter__(self) -> _Response: ...
    def __exit__(self, *args: object) -> None: ...


Opener = Callable[..., _Response]
Fetch = Callable[[str], bytes]


def redact(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def fetch(
    url: str,
    *,
    secrets: Iterable[str] = (),
    timeout: float = 60,
    retries: int = 3,
    backoff: float = 2.0,
    opener: Opener = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """GET `url` and return the body. Retries on rate limits, server errors and network errors."""
    hidden = tuple(secrets)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = ""
    for attempt in range(1, retries + 1):
        try:
            with opener(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in RETRY_STATUS:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = type(e).__name__ + ": " + str(getattr(e, "reason", e))
        if attempt < retries:
            sleep(backoff * attempt)
    raise DownloadError(redact(f"download failed ({last}): {url}", hidden))
