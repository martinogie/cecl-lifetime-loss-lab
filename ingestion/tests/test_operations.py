"""Unit tests for step 8: run log, alerts, health check, tie-outs, snapshots, retention, replay."""

from __future__ import annotations

import json
import subprocess
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cecl import controls
from ingestion import alerts, health, macro_build, retention, run_log, snapshot, tie_outs


@pytest.fixture(autouse=True)
def ci_environment(monkeypatch):
    """These tests run as CECL_ENV=ci, whatever the shell has (CI's unit job sets none)."""
    monkeypatch.setenv("CECL_ENV", "ci")
    monkeypatch.delenv("CECL_WINDOW_EXCEPTION", raising=False)


# ------------------------------------------------------------------ run log


def test_logged_run_writes_success_and_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CECL_CODE_VERSION", "abc123")
    with run_log.logged_run(tmp_path, "freddie", years="2007") as run:
        run.rows["bronze_perf"] = 5
    with pytest.raises(ValueError, match="boom"):
        with run_log.logged_run(tmp_path, "freddie", run_id="r2") as run:
            raise ValueError("boom")
    records = run_log.read_all(tmp_path)
    assert [r["status"] for r in records] == ["success", "failed"]
    assert records[0]["rows"] == {"bronze_perf": 5} and records[0]["params"] == {"years": "2007"}
    assert records[0]["code_version"] == "abc123" and records[0]["env"] == "ci"
    assert records[1]["error"] == "ValueError: boom" and records[1]["duration_seconds"] >= 0
    assert run_log.last_success(tmp_path, "freddie")["run_id"] == records[0]["run_id"]
    assert run_log.last_success(tmp_path, "macro") is None
    assert run_log.read_all(tmp_path / "nothing") == []


def test_code_version_from_git_or_unknown(monkeypatch) -> None:
    monkeypatch.delenv("CECL_CODE_VERSION", raising=False)
    assert run_log.code_version()  # a commit hash in a checkout

    def broken(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(run_log.subprocess, "run", broken)
    assert run_log.code_version() == "unknown"


# ------------------------------------------------------------------ alerts

URLS = {
    "SLACK_WEBHOOK_URL": "https://hooks.example.com/s",
    "DISCORD_WEBHOOK_URL": "https://hooks.example.com/d",
}


def test_alerts_skip_cleanly_when_not_set_up() -> None:
    sent: list[str] = []

    def post(url: str, body: bytes) -> int:
        sent.append(url)
        return 200

    outcome = alerts.send("hi", env={"SLACK_WEBHOOK_URL": "<set-by-martins>"}, post=post, env_name="dev")
    assert outcome == {
        "slack": "skipped: SLACK_WEBHOOK_URL not set",
        "discord": "skipped: DISCORD_WEBHOOK_URL not set",
    }
    assert alerts.send("hi", env=URLS, post=post, env_name="local")["slack"] == "skipped: local never posts"
    assert alerts.send("hi", env=URLS, post=post)["discord"] == "skipped: ci never posts"  # CECL_ENV=ci
    insecure = {"SLACK_WEBHOOK_URL": "http://hooks.example.com/s"}
    assert "not an https URL" in alerts.send("hi", env=insecure, post=post, env_name="dev")["slack"]
    assert sent == []


def test_alerts_send_with_prefix_and_never_raise(capsys) -> None:
    bodies: dict[str, dict] = {}

    def post(url: str, body: bytes) -> int:
        bodies[url] = json.loads(body)
        return 204

    assert alerts.send("x" * 5000, env=URLS, post=post, env_name="prod") == {
        "slack": "sent",
        "discord": "sent",
    }
    assert bodies[URLS["SLACK_WEBHOOK_URL"]]["text"].startswith("[CECL] x")
    assert len(bodies[URLS["DISCORD_WEBHOOK_URL"]]["content"]) == alerts.MAX_LENGTH
    assert alerts.send("y", env=URLS, post=post, env_name="dev") == {"slack": "sent", "discord": "sent"}
    assert bodies[URLS["SLACK_WEBHOOK_URL"]]["text"] == "[CECL dev] y"

    def http_error(url: str, body: bytes) -> int:
        raise urllib.error.HTTPError(url, 404, "gone", {}, None)  # type: ignore[arg-type]

    def network(url: str, body: bytes) -> int:
        raise TimeoutError("slow")

    assert alerts.send("z", env=URLS, post=http_error, env_name="dev")["slack"] == "failed: HTTP 404"
    assert alerts.send("z", env=URLS, post=network, env_name="dev")["discord"] == "failed: TimeoutError"
    assert alerts.send("z", env=URLS, post=lambda u, b: 500, env_name="dev")["slack"] == "failed: HTTP 500"
    assert "hooks.example.com" not in capsys.readouterr().out  # the webhook URL is never printed


def test_post_uses_https_request(monkeypatch) -> None:
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def urlopen(request, timeout):
        seen["data"], seen["timeout"] = request.data, timeout
        return Response()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", urlopen)
    assert alerts._post("https://hooks.example.com/s", b"{}") == 200
    assert seen == {"data": b"{}", "timeout": alerts.TIMEOUT_SECONDS}


def test_failure_callback_alerts_and_logs(tmp_path: Path) -> None:
    ti = SimpleNamespace(dag_id="cecl_macro_data", task_id="fred", run_id="manual__1", try_number=2)
    context = {"task_instance": ti, "exception": RuntimeError("contract failed")}
    assert alerts.failure_text(context) == (
        "FAILED cecl_macro_data.fred (run manual__1, try 2): RuntimeError: contract failed"
    )
    assert alerts.failure_text({}) == "FAILED ?.? (run ?, try ?): see the task log"
    outcome = alerts.failure_callback(tmp_path, "macro")(context)
    assert outcome["slack"] == "skipped: ci never posts"
    (record,) = run_log.read_all(tmp_path)
    assert record["pipeline"] == "macro" and record["status"] == "failed"
    assert record["params"]["task"] == "fred" and "contract failed" in record["error"]
    assert alerts.task_failed(context) == outcome  # no root: alert only


# ------------------------------------------------------------------ health check


def test_health_problems() -> None:
    now = datetime(2026, 11, 18, 13, tzinfo=UTC)

    def rec(pipeline: str, status: str, days: int, **kw) -> dict:
        return {"pipeline": pipeline, "status": status, "run_id": f"{pipeline}{days}",
                "started_at": (now - timedelta(days=days)).isoformat(), **kw}  # fmt: skip

    assert health.problems([rec("macro", "success", 1)], now) == []
    assert health.problems([], now) == ["macro: no successful run on record"]
    found = health.problems(
        [
            rec("macro", "success", 9, stale={"GS10": 70}),
            rec("freddie", "success", 30),
            rec("freddie", "failed", 2),
        ],
        now,
    )
    # a failure recorded under the DAG id counts for its pipeline, and a later success clears it
    assert health.problems([rec("cecl_macro_data", "failed", 2), rec("macro", "success", 1)], now) == []
    assert found == [
        "macro: last successful run 9 days ago",
        "macro: stale sources in the last run: ['GS10']",
        "freddie: the latest run failed (freddie2)",
    ]


# ------------------------------------------------------------------ tie-outs


A, B, C = "F07Q1000001", "F07Q1000002", "F08Q1000003"  # C sits in the 2007 file with a 2008 ID (L-018)
VINTAGE_SQL = (
    "case when substr(loan_id, 2, 2) = '99' then 1999 else 2000 + cast(substr(loan_id, 2, 2) as integer) end"
)


def _freddie_layers(root: Path, drop_silver_row: bool = False) -> duckdb.DuckDBPyConnection:
    """Tiny bronze (all in the 2007 file), silver (views) and panel: loans A and B are vintage 2007;
    C has a 2008 loan ID and no monthly rows yet."""
    dec = pa.decimal128(18, 4)
    orig = pa.table({"loan_id": [A, B, C], "orig_upb": pa.array([Decimal(100000), Decimal(200000),
                     Decimal("150000.50")], dec), "orig_loan_term": [360, 480, 360]})  # fmt: skip
    perf = pa.table({"loan_id": [A, A, B], "current_upb": pa.array([Decimal("99000.25"), Decimal(98000),
                     Decimal(199000)], dec), "derived": [False, True, False]})  # fmt: skip
    for kind, table in (("freddie_orig", orig), ("freddie_perf", perf)):
        folder = root / "bronze" / kind / "vintage=2007"
        folder.mkdir(parents=True)
        pq.write_table(table, folder / "part.parquet")
    panel = pa.table({"loan_id": [A, A, B], "vintage_year": [2007, 2007, 2007],
                      "status": ["current", "prepaid", "defaulted"], "status_not_yet_due": [True, False, False]})  # fmt: skip
    (root / "panel" / "vintage=2007").mkdir(parents=True)
    pq.write_table(panel, root / "panel" / "vintage=2007" / "panel.parquet")

    con = duckdb.connect()
    b = (root / "bronze").as_posix()
    con.execute("create schema silver")
    con.execute(
        f"create view silver.stg_freddie__origination as select loan_id, {VINTAGE_SQL} as vintage_year, "
        "cast(orig_upb as decimal(18, 2)) as orig_upb, orig_loan_term > 360 as term_over_360 "
        f"from read_parquet('{b}/freddie_orig/*/*.parquet')"
    )
    where = f"where not (loan_id = '{B}')" if drop_silver_row else ""
    con.execute(
        "create view silver.stg_freddie__performance as select loan_id, current_upb, derived "
        f"from read_parquet('{b}/freddie_perf/*/*.parquet') {where}"
    )
    con.execute(
        f"create view silver.int_freddie__loan_months as select loan_id, {VINTAGE_SQL} as vintage_year, "
        "cast(current_upb as decimal(18, 2)) as current_upb, derived as remaining_months_derived "
        "from silver.stg_freddie__performance"
    )
    return con


def test_freddie_tie_outs_agree(tmp_path: Path) -> None:
    con = _freddie_layers(tmp_path)
    checks, watch, rows = tie_outs.freddie_tie_outs(
        con, tmp_path / "bronze", tmp_path / "panel", [2007, 2008]
    )
    assert len(checks) == 14 and all(t.ok for t in checks)
    assert controls.enforce(checks) == checks
    assert watch == {
        "2007": {"L-013_remaining_months_derived_rows": 1, "L-014_loans_term_over_360": 1,
                 "L-015_loans_without_monthly_rows": 0, "L-018_loans_from_another_years_file": 0,
                 "xx_at_age_0_loans": 1},
        "2008": {"L-013_remaining_months_derived_rows": 0, "L-014_loans_term_over_360": 0,
                 "L-015_loans_without_monthly_rows": 1, "L-018_loans_from_another_years_file": 1,
                 "xx_at_age_0_loans": 0},
    }  # fmt: skip
    assert rows == {"bronze_orig": 3, "bronze_perf": 3, "silver_loans": 3, "silver_loan_months": 3,
                    "panel_rows": 3}  # fmt: skip
    assert tie_outs.totals({"a": {"x": 1}, "b": {"x": 2, "y": 1}}) == {"x": 3, "y": 1}


def test_freddie_tie_outs_catch_a_dropped_row(tmp_path: Path) -> None:
    con = _freddie_layers(tmp_path, drop_silver_row=True)
    checks, _, _ = tie_outs.freddie_tie_outs(con, tmp_path / "bronze", tmp_path / "panel", [2007, 2008, 2009])
    failed = {t.name for t in checks if not t.ok}
    assert "vintage 2007: loan-months, bronze vs silver" in failed
    assert "vintage 2007: monthly balance, bronze vs silver" in failed
    assert all(t.ok for t in checks if "2008" in t.name or "2009" in t.name)  # 2009 is empty: zero = zero
    with pytest.raises(controls.TieOutError):
        controls.enforce(checks)
    with pytest.raises(ValueError, match="no vintages"):
        tie_outs.freddie_tie_outs(con, tmp_path, tmp_path, [])


# ------------------------------------------------------------------ snapshots and retention

CODE = {"code_commit": "c" * 40, "code_tree": "t" * 40, "code_clean": True}


def _data_root(root: Path) -> None:
    (root / "raw" / "freddie" / "sample").mkdir(parents=True)
    (root / "raw" / "freddie" / "sample" / "sample_2007.zip").write_bytes(b"zip")
    for d in ("2026-11-01", "2026-11-15"):
        (root / "raw" / "macro" / "fhfa" / d).mkdir(parents=True)
        (root / "raw" / "macro" / "fhfa" / d / "hpi.csv").write_text(d)
    (root / "local" / "bronze" / "macro_fhfa").mkdir(parents=True)
    table = pa.table({"geo": ["AA"], "hpi": [1.0], "downloaded_at": [datetime(2026, 11, 15, tzinfo=UTC)]})
    pq.write_table(table, root / "local" / "bronze" / "macro_fhfa" / "fhfa.parquet")
    (root / "local" / "panel").mkdir(parents=True)
    pq.write_table(pa.table({"loan_id": ["A"]}), root / "local" / "panel" / "p.parquet")


def test_snapshot_build_write_verify_and_tag_message(tmp_path: Path) -> None:
    _data_root(tmp_path)
    manifest = snapshot.build(tmp_path, "2026Q2", 1, {"note": 1}, code=CODE)
    assert manifest.name == "cecl-2026Q2-data-v1" and manifest.quarter_end == "2026-06-30"
    paths = [e.path for e in manifest.files]
    assert paths == ["raw/freddie/sample/sample_2007.zip", "raw/macro/fhfa/2026-11-15/hpi.csv",
                     "local/bronze/macro_fhfa/fhfa.parquet", "local/panel/p.parquet"]  # fmt: skip
    bronze = manifest.files[2]
    assert bronze.layer == "bronze" and bronze.rows == 1 and bronze.content_sha256
    path = snapshot.write(tmp_path, manifest)
    assert snapshot.verify(tmp_path, path).ok
    assert json.loads(path.read_text())["fingerprint"] == manifest.fingerprint()

    # a rebuild with a new download time but the same values still matches
    later = pa.table({"geo": ["AA"], "hpi": [1.0], "downloaded_at": [datetime(2026, 12, 1, tzinfo=UTC)]})
    pq.write_table(later, tmp_path / "local" / "bronze" / "macro_fhfa" / "fhfa.parquet")
    assert snapshot.verify(tmp_path, path).ok
    # a changed value, a removed file and a new file are all caught
    pq.write_table(pa.table({"loan_id": ["B"]}), tmp_path / "local" / "panel" / "p.parquet")
    (tmp_path / "raw" / "freddie" / "sample" / "sample_2007.zip").unlink()
    pq.write_table(pa.table({"x": [1]}), tmp_path / "local" / "panel" / "new.parquet")
    result = snapshot.verify(tmp_path, path)
    assert result.as_dict() == {"ok": False, "matched": 2, "changed": ["local/panel/p.parquet"],
                                "missing": ["raw/freddie/sample/sample_2007.zip"],
                                "extra": ["local/panel/new.parquet"]}  # fmt: skip
    message = snapshot.tag_message(manifest)
    assert message.startswith(
        "cecl-2026Q2-data-v1: data snapshot for the quarter ending 2026-06-30 (4 files;"
    )
    assert json.loads(message.split("\n\n", 1)[1])["code_tree"] == "t" * 40


def test_snapshot_names_and_git(tmp_path: Path, monkeypatch) -> None:
    assert snapshot.quarter_end("2026Q4") == "2026-12-31" and snapshot.quarter_end("2027Q1") == "2027-03-31"
    assert snapshot.quarter_end("2026Q3") == "2026-09-30"
    with pytest.raises(ValueError, match="2026Q2"):
        snapshot.snapshot_name("2026-06", 1)
    with pytest.raises(FileNotFoundError):
        snapshot.build(tmp_path, "2026Q2", code=CODE)
    assert snapshot.latest_raw_folders(tmp_path) == []
    # the git lookup, in a throwaway repository
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _data_root(tmp_path)
    for args in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    monkeypatch.chdir(tmp_path)
    manifest = snapshot.build(tmp_path, "2026Q2")
    assert len(manifest.code_tree) == 40 and manifest.code_clean


def test_retention_keeps_latest_and_snapshotted(tmp_path: Path) -> None:
    assert retention.plan(tmp_path).remove == []
    for d in ("2026-10-01", "2026-10-08", "2026-10-15", "2026-10-22"):
        (tmp_path / "raw" / "macro" / "fred" / d).mkdir(parents=True)
        (tmp_path / "raw" / "macro" / "fred" / d / "UNRATE.json").write_text("{}")
    folder = tmp_path / "local" / "snapshots" / "cecl-2026Q2-data-v1"
    folder.mkdir(parents=True)
    files = [{"path": "raw/macro/fred/2026-10-01/UNRATE.json"}, {"path": "local/panel/p.parquet"}]
    (folder / "manifest.json").write_text(json.dumps({"files": files}))
    chosen = retention.plan(tmp_path, keep_latest=2)
    assert chosen.remove == ["raw/macro/fred/2026-10-08"] and chosen.remove_bytes == 2
    assert "raw/macro/fred/2026-10-01" in chosen.keep  # listed in a snapshot: kept forever
    assert retention.apply(tmp_path, chosen) == ["raw/macro/fred/2026-10-08"]
    assert not (tmp_path / "raw" / "macro" / "fred" / "2026-10-08").exists()
    with pytest.raises(ValueError, match="outside raw/macro"):
        retention.apply(tmp_path, retention.Plan(remove=["local/panel"]))


# ------------------------------------------------------------------ replay of saved downloads


def test_replay_fetch_reads_saved_files(tmp_path: Path) -> None:
    (tmp_path / "UNRATE.json").write_bytes(b"u")
    (tmp_path / "hpi_at_state.csv").write_bytes(b"h")
    get = macro_build.replay_fetch(tmp_path)
    assert get("https://api.stlouisfed.org/fred/series/observations?series_id=UNRATE&api_key=k") == b"u"
    assert get("https://www.fhfa.gov/hpi/download/quarterly_datasets/hpi_at_state.csv") == b"h"
    with pytest.raises(FileNotFoundError, match="nope.csv"):
        get("https://example.com/nope.csv")
    assert macro_build.stale_sources({"a": {"stale": {"X": 9}}, "b": {"stale": {}}, "c": 1}) == {"X": 9}


def test_replay_rebuilds_bronze_from_raw(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple] = []

    def fake(name):
        def loader(root, run_date, **kw):
            calls.append((name, run_date, sorted(kw)))
            return {"source": name}

        return loader

    monkeypatch.setattr(macro_build, "load_fred", fake("fred"))
    monkeypatch.setattr(macro_build, "load_fhfa", fake("fhfa"))
    monkeypatch.setattr(macro_build, "load_fed", fake("fed"))
    monkeypatch.setattr(macro_build, "build_silver", lambda root: {"silver_rows": {}})
    out = macro_build.replay(tmp_path, date(2026, 11, 15))
    assert set(out) == {"fred", "fhfa", "fed", "silver"}
    assert calls[0] == ("fred", date(2026, 11, 15), ["fetch", "key", "pause"])
    with pytest.raises(ValueError, match="unknown source"):
        macro_build.replay(tmp_path, date(2026, 11, 15), ("bls",))


def test_success_text_and_duration() -> None:
    assert (
        alerts.duration(None) == "?" and alerts.duration(42.4) == "42s" and alerts.duration(252) == "4m 12s"
    )
    assert alerts.success_text("cecl_macro_data", "manual__1", 61, "all good") == (
        "OK cecl_macro_data (run manual__1, 1m 01s): all good"
    )
    assert alerts.success_text("cecl_health_check", "r", 3, "macro: stale", warning=True).startswith(
        "WARNING "
    )
