from __future__ import annotations

from pathlib import Path

import pytest

from ingestion import ml_preflight as mp

GB = mp.GB


def test_preflight_passes_on_a_ready_machine(tmp_path: Path) -> None:
    result = mp.check(tmp_path, memory=lambda: 5.0, containers=lambda: [], disk_free=lambda p: 20 * GB)
    assert result.ok and mp.enforce(result) is result
    assert result.as_dict() == {
        "free_memory_gb": 5.0,
        "free_disk_gb": 20.0,
        "airflow_running": [],
        "problems": [],
    }


def test_preflight_lists_every_problem(tmp_path: Path) -> None:
    result = mp.check(
        tmp_path,
        memory=lambda: 1.2,
        containers=lambda: ["cecl-dev-airflow-scheduler-1", "cecl-dev-airflow-apiserver-1"],
        disk_free=lambda p: 2 * GB,
    )
    assert not result.ok and len(result.problems) == 3
    with pytest.raises(mp.PreflightFailed) as info:
        mp.enforce(result)
    text = str(info.value)
    assert "dev Airflow is running (2 containers)" in text
    assert "free memory 1.2 GB, need 4.0 GB" in text and "free disk 2.0 GB, need 5.0 GB" in text


def test_running_airflow_containers(monkeypatch) -> None:
    class Done:
        stdout = "cecl-dev-postgres-1\ncecl-dev-airflow-scheduler-1\nbank-health-dev-airflow-scheduler-1\n"

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: Done())
    assert mp.running_airflow_containers() == ["cecl-dev-airflow-scheduler-1"]  # never Bank Health's

    def no_docker(*a, **k):
        raise OSError("docker not found")

    monkeypatch.setattr(mp.subprocess, "run", no_docker)
    assert mp.running_airflow_containers() == []
