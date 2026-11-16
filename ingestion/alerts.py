"""Alerts to Slack and Discord: failures and stale data (control C18).

Rules:
- Webhook URLs come from SLACK_WEBHOOK_URL and DISCORD_WEBHOOK_URL (each stack's .env; Martins sets
  them). A missing, empty or placeholder value ("<set-by-martins>") means "not set up yet": that
  channel is skipped cleanly, with one log line, and nothing fails.
- Only Airflow stacks (CECL_ENV dev or prod) post. Runs on a laptop (local) and in CI never post,
  even if a webhook is set, so testing can't spam a channel.
- Messages start with [CECL] (prod) or [CECL dev] (dev), so they never look like Bank Health's.
- An alert never raises: a broken webhook must not hide the failure being reported, or fail a run
  that worked. The outcome per channel ("sent", "skipped: ...", "failed: ...") goes in the run log.
- A webhook URL is a secret (anyone holding it can post): it is never printed or logged, and error
  messages carry only the error type and HTTP status.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from typing import Any

from cecl import environment

CHANNELS = {"slack": "SLACK_WEBHOOK_URL", "discord": "DISCORD_WEBHOOK_URL"}
POSTING_ENVIRONMENTS = ("dev", "prod")
TIMEOUT_SECONDS = 10
MAX_LENGTH = 1800  # Discord's limit is 2000 characters; Slack's is far higher

Post = Callable[[str, bytes], int]  # (url, JSON body) -> HTTP status


def prefix(env_name: str) -> str:
    return "[CECL]" if env_name == "prod" else f"[CECL {env_name}]"


def webhook(var: str, env: Mapping[str, str] | None = None) -> str | None:
    """The webhook URL, or None if it is missing, empty or still a placeholder."""
    value = (env if env is not None else os.environ).get(var, "").strip()
    if not value or value.startswith("<"):
        return None
    return value


def _post(url: str, body: bytes) -> int:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": "cecl-alerts/1.0"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 (https only)
        return int(response.status)


def _payload(channel: str, text: str) -> bytes:
    key = "content" if channel == "discord" else "text"
    return json.dumps({key: text}).encode()


def send(
    text: str,
    env: Mapping[str, str] | None = None,
    post: Post = _post,
    env_name: str | None = None,
) -> dict[str, str]:
    """Send `text` to every configured channel. Returns the outcome per channel; never raises."""
    name = env_name or environment.current_env()
    if name not in POSTING_ENVIRONMENTS:
        outcome = {c: f"skipped: {name} never posts" for c in CHANNELS}
        print(f"alerts: {outcome['slack']}")
        return outcome
    message = f"{prefix(name)} {text}"[:MAX_LENGTH]
    outcome = {}
    for channel, var in CHANNELS.items():
        url = webhook(var, env)
        if url is None:
            outcome[channel] = f"skipped: {var} not set"
        elif not url.startswith("https://"):
            outcome[channel] = f"skipped: {var} is not an https URL"
        else:
            try:
                status = post(url, _payload(channel, message))
                outcome[channel] = "sent" if 200 <= status < 300 else f"failed: HTTP {status}"
            except urllib.error.HTTPError as e:
                outcome[channel] = f"failed: HTTP {e.code}"
            except Exception as e:  # noqa: BLE001 (an alert must never break the run)
                outcome[channel] = f"failed: {type(e).__name__}"
        print(f"alerts: {channel} {outcome[channel]}")
    return outcome


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def success_text(dag_id: str, run_id: str, seconds: float | None, summary: str, warning: bool = False) -> str:
    """The one line every successful Airflow run ends with (sent by the DAG's last task, so it only
    goes out when every task succeeded). WARNING means the run worked but found something to look at."""
    word = "WARNING" if warning else "OK"
    return f"{word} {dag_id} (run {run_id}, {duration(seconds)}): {summary}"


def failure_text(context: Mapping[str, Any]) -> str:
    """One line from an Airflow task-failure context: which task, which run, where the log is."""
    ti = context.get("task_instance") or context.get("ti")
    dag_id = getattr(ti, "dag_id", "?")
    task_id = getattr(ti, "task_id", "?")
    run_id = getattr(ti, "run_id", context.get("run_id", "?"))
    tries = getattr(ti, "try_number", "?")
    error = context.get("exception")
    reason = f"{type(error).__name__}: {str(error)[:300]}" if error else "see the task log"
    return f"FAILED {dag_id}.{task_id} (run {run_id}, try {tries}): {reason}"


def task_failed(
    context: Mapping[str, Any], root: Path | None = None, pipeline: str | None = None
) -> dict[str, str]:
    """Airflow on_failure_callback: alert, and add a failed record to the run log.

    `pipeline` is the run-log name the DAG's successful runs use ("macro", "health_check"), so a
    later success replaces the failure as that pipeline's latest run."""
    text = failure_text(context)
    outcome = send(text)
    if root is not None:
        from ingestion import run_log  # local import: keeps this module light for the DAG parser

        ti = context.get("task_instance") or context.get("ti")
        record = run_log.new_record(
            pipeline or str(getattr(ti, "dag_id", "airflow")),
            {"task": getattr(ti, "task_id", None), "airflow_run_id": getattr(ti, "run_id", None)},
        )
        record.alerts = outcome
        run_log.write(root, run_log.finish(record, RuntimeError(text)))
    return outcome


def failure_callback(root: Path, pipeline: str) -> Callable[[Mapping[str, Any]], dict[str, str]]:
    """The callback to put in a DAG's default_args, bound to the data root and pipeline name."""
    return partial(task_failed, root=root, pipeline=pipeline)
