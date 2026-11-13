from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cecl import environment, scheduling
from ingestion.preflight import task_preflight

# November 2026 is Central Standard Time: Chicago = UTC - 6 hours.
WEDNESDAY_7AM_CT = datetime(2026, 11, 11, 13, 0, tzinfo=UTC)
MONDAY_6AM_CT = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
TUESDAY_3AM_CT = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("name", ["dev", "prod"])
def test_preflight_passes_for_airflow_envs(monkeypatch, capsys, name):
    monkeypatch.setenv("CECL_ENV", name)
    assert task_preflight(WEDNESDAY_7AM_CT).name == name
    assert f"environment={name}" in capsys.readouterr().out


def test_preflight_fails_without_env(monkeypatch):
    monkeypatch.delenv("CECL_ENV", raising=False)
    with pytest.raises(environment.EnvironmentNotSet):
        task_preflight(WEDNESDAY_7AM_CT)


def test_preflight_fails_for_local(monkeypatch):
    monkeypatch.setenv("CECL_ENV", "local")
    with pytest.raises(environment.EnvironmentMismatch):
        task_preflight(WEDNESDAY_7AM_CT)


@pytest.mark.parametrize(("moment", "reason"), [(MONDAY_6AM_CT, "Bank Health"), (TUESDAY_3AM_CT, "reboot")])
def test_preflight_fails_in_blocked_windows(monkeypatch, moment, reason):
    monkeypatch.setenv("CECL_ENV", "prod")
    with pytest.raises(scheduling.BlockedWindow, match=reason):
        task_preflight(moment)
