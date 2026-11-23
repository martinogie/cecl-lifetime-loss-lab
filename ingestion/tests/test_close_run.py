"""The quarterly close's VM steps (ingestion/close_run.py), the Databricks client (ingestion/databricks_api.py),
the weekly monitoring steps (ingestion/monitor_run.py) and the committee pack draft, with fakes only: no
workspace, no token, no loan data."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from ingestion import close_run, committee_pack, monitor_run
from ingestion import databricks_api as dbx


@pytest.fixture(autouse=True)
def prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CECL_ENV", "prod")
    monkeypatch.setenv("CECL_DATABRICKS_LAYOUT", "catalogs")


# ---------------------------------------------------------------- request, time, overlays, commit
def test_check_request() -> None:
    today = date(2027, 1, 25)
    assert close_run.check_request("202609", today, "202606") == "202609"
    assert close_run.check_request(" 202612 ", today, "202609") == "202612"
    for bad, last in (("202608", "202606"), ("202703", "202612"), ("202606", "202606"), ("202612", "202606")):
        with pytest.raises(close_run.CloseRefused):
            close_run.check_request(bad, today, last)
    with pytest.raises(close_run.CloseRefused, match="next one is 202609"):
        close_run.check_request("202612", today, "202606")


def test_time_budget() -> None:
    ok = datetime(2027, 1, 27, 14, 0, tzinfo=UTC)  # Wednesday 08:00 Chicago
    close_run.check_time_budget(ok)
    with pytest.raises(close_run.CloseRefused, match="02:30"):
        close_run.check_time_budget(datetime(2027, 1, 28, 5, 0, tzinfo=UTC))  # Wednesday 23:00 Chicago
    with pytest.raises(close_run.CloseRefused, match="Monday"):
        close_run.check_time_budget(datetime(2027, 1, 25, 3, 0, tzinfo=UTC))  # Sunday 21:00 Chicago


def test_overlay_review(tmp_path: Path) -> None:
    log = tmp_path / "overlay_log.json"
    log.write_text(json.dumps({"overlays": [], "assessments": [{"id": "A", "reserve_ym": "202606"}]}))
    assert close_run.check_overlay_review(log, "202606") == 1
    with pytest.raises(close_run.CloseRefused, match="no overlay review for 202609"):
        close_run.check_overlay_review(log, "202609")


def test_vm_commit(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
    assert close_run.vm_commit(git) == "a" * 40
    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack-refs\n" + "b" * 40 + " refs/heads/main\n")
    assert close_run.vm_commit(git) == "b" * 40
    (git / "HEAD").write_text("c" * 40)
    assert close_run.vm_commit(git) == "c" * 40


# ---------------------------------------------------------------- a fake workspace
class FakeWs:
    def __init__(self) -> None:
        self.commit = "f" * 40
        self.uploads: dict[str, bytes] = {}
        self.files: dict[str, list[dict[str, Any]]] = {}
        self.blobs: dict[str, bytes] = {}

    def deployed_commit(self, name: str) -> str:
        return self.commit

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        return self.files.get(path, [])

    def upload(self, data: bytes, path: str) -> None:
        self.uploads[path] = data

    def download(self, path: str) -> bytes:
        return self.blobs[path]

    def model_alias(self, name: str, alias: str) -> dict[str, str]:
        return {"version": "1", "run_id": f"run-{name.rsplit('_', 1)[-1]}"}


VOL = "/Volumes/cecl_lab/bronze/raw"


def test_check_deploys() -> None:
    ws = FakeWs()
    assert set(close_run.check_deploys(ws, "prod", "f" * 40)) == set(close_run.CLOSE_JOBS)  # type: ignore[arg-type]
    with pytest.raises(close_run.CloseRefused, match="another commit"):
        close_run.check_deploys(ws, "prod", "e" * 40)  # type: ignore[arg-type]


def test_check_freddie_and_last_close() -> None:
    ws = FakeWs()
    t_old, t_new = 1_700_000_000_000, 1_800_000_000_000
    ws.files[f"{VOL}/freddie/sample"] = [{"name": f"sample_{y}.zip", "size": 10, "last_modified": t_new, "is_directory": False}
                                         for y in range(1999, 2027)]  # fmt: skip
    cut = datetime.fromtimestamp(1_750_000_000, UTC)
    assert close_run.check_freddie(ws, range(1999, 2027), cut)["files"] == 28  # type: ignore[arg-type]
    ws.files[f"{VOL}/freddie/sample"][3]["last_modified"] = t_old
    with pytest.raises(close_run.CloseRefused, match="not updated"):
        close_run.check_freddie(ws, range(1999, 2027), cut)  # type: ignore[arg-type]
    with pytest.raises(close_run.CloseRefused, match="missing"):
        close_run.check_freddie(ws, range(1999, 2028), None)  # type: ignore[arg-type]
    ws.files[f"{VOL}/results/close"] = [
        {"name": n, "is_directory": True} for n in ("202512", "202603", "202606", "tmp")
    ]
    assert close_run.last_close(ws) == "202606"  # type: ignore[arg-type]


def test_upload_macro_and_projection_params(tmp_path: Path) -> None:
    ws = FakeWs()
    macro = tmp_path / "local" / "silver" / "macro"
    macro.mkdir(parents=True)
    with pytest.raises(close_run.CloseRefused, match="incomplete"):
        close_run.upload_macro(ws, tmp_path)  # type: ignore[arg-type]
    for t in ("state_monthly", "national_monthly", "long_run_averages", "fed_paths", "household_monthly"):
        (macro / f"{t}.parquet").write_bytes(b"x")
    sent = close_run.upload_macro(ws, tmp_path)  # type: ignore[arg-type]
    assert "household_monthly" in sent and f"{VOL}/macro_silver/fed_paths.parquet" in ws.uploads
    p = close_run.projection_params(ws, "202609")  # type: ignore[arg-type]
    assert p["plan"] == "202609@202609,202609@202606" and json.loads(p["runs"])["30"] == "run-30"


def test_fetch_and_summary(tmp_path: Path) -> None:
    ws = FakeWs()
    rec, mon = close_record(), {"default_cells_failed": 1, "retrain": {"due": False}, "alerts": ["x"]}
    ws.blobs[f"{VOL}/results/close/202609/close.json"] = json.dumps(rec).encode()
    ws.blobs[f"{VOL}/monitoring/quarterly/202609.json"] = json.dumps(mon).encode()
    files = close_run.fetch(ws, "202609", tmp_path)  # type: ignore[arg-type]
    assert files["close"] == tmp_path / "close" / "202609" / "close.json"
    line = close_run.summary_line(json.loads(files["close"].read_text()), mon)
    assert "total allowance $300.0m" in line and "to the cent: True" in line and "1 default cells" in line


# ---------------------------------------------------------------- committee pack
def block(total: float) -> dict[str, Any]:
    return {"book": {"loans": 1000, "balance": 1e9}, "weighted_reserve": total - 10e6,
            "defaulted_pipeline": {"reserve": 10e6}, "overlays": {"booked_total": 0.0, "assessments": [{"id": "A-1", "decision": "No overlay"}]},
            "total_allowance": total, "total_allowance_ratio": total / 1e9, "fed_scenario_year": 2026,
            "scenarios": {"baseline": {"reserve": 1e8}, "adverse": {"reserve": 4e8}, "severely_adverse": {"reserve": 9e8}},
            "weight_sensitivity": [{"severe_weight": 0.05, "reserve": 2.5e8}]}  # fmt: skip


def close_record() -> dict[str, Any]:
    return {"reserve_ym": "202609", "prior_ym": "202606", "dates": {"202606": block(297_497_504.04), "202609": block(3e8)},
            "rollforward": {"total": {"start": "297497504.04", "provision": "3000000.00", "write_offs": "-600000.00",
                                      "recoveries": "102495.96", "end": "300000000.00"},
                            "reconciles_to_the_cent": True, "write_off_events": 12, "estimated_write_off_events": 3,
                            "by_group": [{"group": "2019-2021", "start": "1", "provision": "1", "write_offs": "0",
                                          "recoveries": "0", "end": "2"}]},
            "bridge": {"steps": {"start": 297.5e6, "exits": -1e6, "end": 300e6}, "reconciles": True, "ties_to_total_allowance": True},
            "controls": {"tie_outs": True, "rollforward_reconciles_to_the_cent": True, "all_pass": True},
            "reserve_adequacy": [{"reserve_ym": "202606", "total_allowance": 297.5e6, "months_elapsed": 3,
                                  "net_write_offs_since": 0.5e6, "share_of_reserve_used": 0.5 / 297.5}],
            "snapshot": {"code_version": "abc", "delta_versions": {"cecl_lab.gold.loan_panel": 7},
                         "pd_models": {"cecl_lab.ml.pd_v2_transition_current": {"version": "3", "alias": "challenger", "run_id": "d0e07d92xx"}},
                         "lgd_model_sha256": "0" * 64, "overlay_log_sha256": "1" * 64}}  # fmt: skip


def test_committee_pack_renders_every_section() -> None:
    md = committee_pack.render(
        close_record(), {"default_cells_failed": 0, "alerts": [], "retrain": {"due": False}}
    )
    for heading in ("# Allowance committee pack: 30 September 2026", "## 2. Rollforward", "## 3. Reserve bridge",
                    "## 6. Controls", "## 7. Model monitoring", "## 9. Reproducibility", "## 11. Sign-off"):  # fmt: skip
        assert heading in md
    assert "**$300,000,000.00**" in md and "| Maker (prepared the close) | | | |" in md
    assert "DRAFT for review" in md and "not an audited bank reserve" in md
    assert committee_pack.quarter_folder("202609") == "2026Q3"


# ---------------------------------------------------------------- Databricks client
class Resp(io.BytesIO):
    def __enter__(self) -> Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def fake_opener(routes: dict[str, Any], calls: list[Any]) -> Any:
    def open_(req: Any, timeout: float = 0) -> Resp:
        calls.append(req)
        for key, body in routes.items():
            if key in req.full_url:
                if isinstance(body, Exception):
                    raise body
                return Resp(body if isinstance(body, bytes) else json.dumps(body).encode())
        raise AssertionError(f"unexpected call {req.full_url}")

    return open_


def test_workspace_jobs_and_files() -> None:
    calls: list[Any] = []
    routes = {
        "/jobs/list": {"jobs": [{"job_id": 7, "settings": {"name": "cecl_lab_monitoring_prod", "tags": {"git_commit": "abc"}}}]},
        "/jobs/run-now": {"run_id": 99},
        "/jobs/runs/get-output": {"notebook_output": {"result": '{"ok": 1}'}},
        "/jobs/runs/get": {"state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}, "run_duration": 120000,
                           "tasks": [{"task_key": "monitoring", "run_id": 5}]},
        "/fs/directories": {"contents": [{"name": "b.zip", "file_size": 3, "last_modified": 9}, {"name": "a.zip"}]},
        "/unity-catalog/models": {"version": 3, "run_id": "r1"},
        "/fs/files": b"payload",
    }  # fmt: skip
    ws = dbx.Workspace(
        "https://example.invalid", "secret-token", fake_opener(routes, calls), sleep=lambda s: None
    )
    assert ws.deployed_commit("cecl_lab_monitoring_prod") == "abc"
    out = ws.run_and_wait("cecl_lab_monitoring_prod", {"mode": "weekly"})
    assert out["result"] == "SUCCESS" and out["minutes"] == 2.0 and out["tasks"]["monitoring"] == '{"ok": 1}'
    assert [e["name"] for e in ws.list_dir(f"{VOL}/freddie/sample")] == ["a.zip", "b.zip"]
    assert ws.download(f"{VOL}/x.json") == b"payload"
    assert ws.model_alias("cecl_lab.ml.m", "challenger") == {"version": "3", "run_id": "r1"}
    assert all(c.headers["Authorization"] == "Bearer secret-token" for c in calls)
    assert "secret-token" not in repr(ws)
    with pytest.raises(dbx.DatabricksError):
        ws.download("/tmp/not-a-volume")
    with pytest.raises(dbx.DatabricksError, match="0 found"):
        ws.job("cecl_lab_other_prod")


def test_workspace_errors_never_show_the_token() -> None:
    err = urllib.error.HTTPError("https://example.invalid/x?token=secret-token", 403, "no", {}, None)  # type: ignore[arg-type]
    ws = dbx.Workspace(
        "https://example.invalid", "secret-token", fake_opener({"/jobs/list": err}, []), sleep=lambda s: None
    )
    with pytest.raises(dbx.DatabricksError) as e:
        ws.job("x")
    assert "secret-token" not in str(e.value) and "HTTP 403" in str(e.value)


def test_failed_run_raises() -> None:
    routes = {"/jobs/list": {"jobs": [{"job_id": 1, "settings": {"name": "j"}}]}, "/jobs/run-now": {"run_id": 2},
              "/jobs/runs/get": {"state": {"life_cycle_state": "TERMINATED", "result_state": "FAILED", "state_message": "boom"},
                                 "tasks": []}}  # fmt: skip
    ws = dbx.Workspace("https://example.invalid", "t", fake_opener(routes, []), sleep=lambda s: None)
    with pytest.raises(dbx.DatabricksError, match="ended FAILED: boom"):
        ws.run_and_wait("j", {})


def test_workspace_from_env() -> None:
    with pytest.raises(dbx.DatabricksError, match="DATABRICKS_HOST and DATABRICKS_TOKEN not set"):
        dbx.workspace({})
    ws = dbx.workspace({"DATABRICKS_HOST": "example.invalid", "DATABRICKS_TOKEN": "t"})
    assert (
        ws.host == "https://example.invalid"
        and dbx.job_name("close_pricing", "prod") == "cecl_lab_close_pricing_prod"
    )


# ---------------------------------------------------------------- weekly monitoring
def test_latest_months_and_weekly_record(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    macro = tmp_path / "local" / "silver" / "macro"
    macro.mkdir(parents=True)
    pq.write_table(
        pa.table({"month": [date(2026, 9, 1), date(2026, 10, 1)], "value": [1.0, 2.0]}),
        macro / "national_monthly.parquet",
    )
    pq.write_table(pa.table({"month": [date(2026, 9, 1), date(2026, 7, 1)], "variable": ["unemployment", "hpi"],
                             "value": [4.0, 300.0]}), macro / "state_monthly.parquet")  # fmt: skip
    latest = monitor_run.latest_months(tmp_path)
    assert latest == {"freddie_performance": "202606", "fred_national": "202610", "fred_state_unemployment": "202609",
                      "fhfa_hpi": "202607", "ffiec_call_reports": None}  # fmt: skip
    rec = monitor_run.record(tmp_path, date(2026, 11, 25), latest, None)
    assert rec["status"] == "red" and not rec["scored"] and monitor_run.last_scored(tmp_path) is None
    rec = monitor_run.record(
        tmp_path, date(2026, 11, 25), latest, {"psi": {"current->default": 0.02}, "book_month": "202606"}
    )
    assert rec["scored"] and monitor_run.last_scored(tmp_path) == "202610"
    assert "PSI: current->default 0.02" in monitor_run.summary_line(rec)
    assert (tmp_path / "monitoring" / "weekly" / "2026-11-25.json").exists()
