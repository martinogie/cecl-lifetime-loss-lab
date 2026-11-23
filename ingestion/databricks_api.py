"""The VM's link to the Databricks workspace (Phase 8): start CECL's jobs, wait for them, move small files.

Airflow on the VM orchestrates; Databricks computes (ADR-001). The quarterly close and the monitoring DAGs
use this module to start the prod jobs by name, wait for them, read their results, and move small files
(economic tables up, aggregate results down) through the Files API. Standard library only (no extra
package in the Airflow image).

Needs `DATABRICKS_HOST` and `DATABRICKS_TOKEN` in the stack's .env (Martins adds them; never printed or
logged: every error message passes through `http.redact`). Jobs are found by their bundle name
(`cecl_lab_<job>_<env>`), and each must carry the tag `git_commit` (set by the bundle at deploy): the close
refuses to run jobs deployed from a different commit than the VM's code (control C08, exact re-runs).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ingestion import http

HOST_VAR, TOKEN_VAR = "DATABRICKS_HOST", "DATABRICKS_TOKEN"
TERMINAL = ("TERMINATED", "SKIPPED", "INTERNAL_ERROR")


class DatabricksError(RuntimeError):
    """A Databricks call failed. The message never contains the token."""


@dataclass
class Workspace:
    host: str
    token: str
    opener: Callable[..., Any] = urllib.request.urlopen
    sleep: Callable[[float], None] = time.sleep

    def __repr__(self) -> str:  # never show the token, even in a traceback
        return "Workspace(host=***, token=***)"

    # ------------------------------------------------------------ plumbing
    def _call(self, method: str, path: str, body: bytes | None = None, query: Mapping[str, Any] | None = None,
              content_type: str = "application/json", retries: int = 3) -> bytes:  # fmt: skip
        url = self.host.rstrip("/") + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": content_type, "User-Agent": http.USER_AGENT})  # fmt: skip
        last = ""
        for attempt in range(1, retries + 1):
            try:
                with self.opener(req, timeout=120) as r:
                    return bytes(r.read())
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} on {method} {path}"
                if e.code not in http.RETRY_STATUS:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"{type(e).__name__} on {method} {path}"
            if attempt < retries:
                self.sleep(2.0 * attempt)
        raise DatabricksError(http.redact(last, (self.token, self.host)))

    def _json(self, method: str, path: str, payload: Mapping[str, Any] | None = None,
              query: Mapping[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
        body = json.dumps(payload).encode() if payload is not None else None
        raw = self._call(method, path, body, query)
        return dict(json.loads(raw)) if raw.strip() else {}

    # ------------------------------------------------------------ jobs
    def job(self, name: str) -> dict[str, Any]:
        found = [j for j in self._json("GET", "/api/2.1/jobs/list", query={"name": name}).get("jobs", [])
                 if j.get("settings", {}).get("name") == name]  # fmt: skip
        if len(found) != 1:
            raise DatabricksError(f"job {name!r}: {len(found)} found (deploy the bundle first)")
        return found[0]

    def deployed_commit(self, name: str) -> str | None:
        return self.job(name).get("settings", {}).get("tags", {}).get("git_commit")

    def run_now(self, name: str, params: Mapping[str, str]) -> int:
        job_id = self.job(name)["job_id"]
        out = self._json("POST", "/api/2.1/jobs/run-now", {"job_id": job_id, "job_parameters": dict(params)})
        return int(out["run_id"])

    def wait(self, run_id: int, poll: float = 30.0, timeout: float = 4 * 3600) -> dict[str, Any]:
        """Wait for a run to end; return its state, duration and each task's notebook result."""
        waited = 0.0
        while True:
            run = self._json("GET", "/api/2.1/jobs/runs/get", query={"run_id": run_id})
            state = run.get("state", {})
            if state.get("life_cycle_state") in TERMINAL:
                break
            if waited >= timeout:
                raise DatabricksError(f"run {run_id} still running after {timeout / 3600:.1f} hours")
            self.sleep(poll)
            waited += poll
        results = {}
        for t in run.get("tasks", []):
            out = self._json("GET", "/api/2.1/jobs/runs/get-output", query={"run_id": t["run_id"]})
            results[t["task_key"]] = (out.get("notebook_output") or {}).get("result") or out.get("error", "")
        return {"run_id": run_id, "result": state.get("result_state") or state.get("life_cycle_state"),
                "message": str(state.get("state_message", ""))[:300],
                "minutes": round(float(run.get("run_duration") or run.get("execution_duration") or 0) / 60000, 2),
                "tasks": results}  # fmt: skip

    def run_and_wait(self, name: str, params: Mapping[str, str], **kw: Any) -> dict[str, Any]:
        out = self.wait(self.run_now(name, params), **kw)
        if out["result"] != "SUCCESS":
            raise DatabricksError(f"{name} run {out['run_id']} ended {out['result']}: {out['message']}")
        return out

    # ------------------------------------------------------------ files in a volume
    def upload(self, data: bytes, volume_path: str) -> None:
        self._call("PUT", "/api/2.0/fs/files" + _quote(volume_path), data, {"overwrite": "true"},
                   content_type="application/octet-stream")  # fmt: skip

    def download(self, volume_path: str) -> bytes:
        return self._call("GET", "/api/2.0/fs/files" + _quote(volume_path))

    def list_dir(self, volume_path: str) -> list[dict[str, Any]]:
        """The entries of a volume folder: name, size and last-modified time (milliseconds since 1970)."""
        out = self._json("GET", "/api/2.0/fs/directories" + _quote(volume_path))
        return sorted(({"name": str(e.get("name")), "size": int(e.get("file_size") or 0),
                        "last_modified": int(e.get("last_modified") or 0), "is_directory": bool(e.get("is_directory"))}
                       for e in out.get("contents", [])), key=lambda e: e["name"])  # fmt: skip

    # ------------------------------------------------------------ models
    def model_alias(self, full_name: str, alias: str) -> dict[str, str]:
        """The Unity Catalog model version an alias points to: version and training run."""
        out = self._json(
            "GET", f"/api/2.1/unity-catalog/models/{urllib.parse.quote(full_name)}/aliases/{alias}"
        )
        return {"version": str(out.get("version", "")), "run_id": str(out.get("run_id", ""))}


def _quote(path: str) -> str:
    if not path.startswith("/Volumes/"):
        raise DatabricksError("only paths under /Volumes/ are used")
    return urllib.parse.quote(path)


def workspace(env: Mapping[str, str] | None = None) -> Workspace:
    e = env if env is not None else os.environ
    host, token = e.get(HOST_VAR, "").strip(), e.get(TOKEN_VAR, "").strip()
    missing = [v for v, x in ((HOST_VAR, host), (TOKEN_VAR, token)) if not x or x.startswith("<")]
    if missing:
        raise DatabricksError(
            f"{' and '.join(missing)} not set: Martins adds them to the stack's .env (never in git)"
        )
    if not host.startswith("https://"):
        host = "https://" + host
    return Workspace(host, token)


def job_name(job: str, env: str) -> str:
    """Bundle name of a CECL job, e.g. cecl_lab_close_pricing_prod."""
    return f"cecl_lab_{job}_{env}"
