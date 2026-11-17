from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cecl import environment, tracking


def test_tracking_uris_follow_the_profile(monkeypatch) -> None:
    monkeypatch.delenv(tracking.PROFILE_VAR, raising=False)
    assert tracking.tracking_uris() == ("databricks://cecl", "databricks-uc://cecl")
    monkeypatch.setenv(tracking.PROFILE_VAR, "other")
    assert tracking.tracking_uris() == ("databricks://other", "databricks-uc://other")


def test_snapshot_and_standard_tags(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "local" / "snapshots" / "cecl-2026Q2-data-v1"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"fingerprint": "abc123", "files": []}))
    monkeypatch.setenv("CECL_ENV", "dev")
    monkeypatch.delenv(tracking.SNAPSHOT_VAR, raising=False)
    tags = tracking.standard_tags(tmp_path)
    assert tags["cecl_env"] == "dev" and tags["data_snapshot"] == "cecl-2026Q2-data-v1"
    assert tags["data_fingerprint"] == "abc123" and tags["git_commit"]
    with pytest.raises(FileNotFoundError, match="cecl-2026Q3-data-v1"):
        tracking.snapshot_tags(tmp_path, "cecl-2026Q3-data-v1")


def test_git_commit_without_git(monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(tracking.subprocess, "run", broken)
    assert tracking.git_commit() == "unknown"


class FakeMlflow:
    def __init__(self) -> None:
        self.calls: dict[str, str] = {}

    def set_tracking_uri(self, uri: str) -> None:
        self.calls["tracking"] = uri

    def set_registry_uri(self, uri: str) -> None:
        self.calls["registry"] = uri

    def set_experiment(self, path: str) -> SimpleNamespace:
        self.calls["experiment"] = path
        return SimpleNamespace(name=path)


def test_configure_points_at_the_environment_and_pins_the_profile(monkeypatch) -> None:
    monkeypatch.setenv("CECL_ENV", "dev")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "bank-health")  # must never be used by CECL
    monkeypatch.delenv(tracking.PROFILE_VAR, raising=False)
    fake = FakeMlflow()
    path = tracking.configure(fake, workspace_user="someone")
    assert path == "/Users/someone/cecl-lifetime-loss-lab-dev"
    assert fake.calls == {
        "tracking": "databricks://cecl",
        "registry": "databricks-uc://cecl",
        "experiment": path,
    }
    import os

    assert os.environ["DATABRICKS_CONFIG_PROFILE"] == "cecl"


@pytest.mark.parametrize("env", ["local", "ci"])
def test_configure_refuses_environments_without_an_experiment(monkeypatch, env) -> None:
    monkeypatch.setenv("CECL_ENV", env)
    with pytest.raises(environment.EnvironmentMismatch):
        tracking.configure(FakeMlflow(), workspace_user="someone")
