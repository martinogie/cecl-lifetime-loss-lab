from __future__ import annotations

from collections import namedtuple

import pytest

from ingestion import disk

Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def free_gb(monkeypatch):
    def _set(gb: float) -> None:
        monkeypatch.setattr(disk.shutil, "disk_usage", lambda _p: Usage(100 * disk.GB, 0, int(gb * disk.GB)))

    return _set


def test_room_when_enough_space(free_gb, tmp_path):
    free_gb(10)
    assert disk.check_room(tmp_path, 5 * disk.GB) == 10 * disk.GB


def test_stops_when_output_would_eat_the_reserve(free_gb, tmp_path):
    free_gb(10)
    with pytest.raises(
        disk.NotEnoughDisk, match="10.0 GB free, this step needs about 7.5 GB and 3 GB must stay free"
    ):
        disk.check_room(tmp_path, int(7.5 * disk.GB))


def test_custom_reserve(free_gb, tmp_path):
    free_gb(4)
    disk.check_room(tmp_path, 3 * disk.GB, reserve_bytes=disk.GB)
    with pytest.raises(disk.NotEnoughDisk):
        disk.check_room(tmp_path, 3 * disk.GB)


def test_negative_size_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        disk.check_room(tmp_path, -1)


def test_path_that_does_not_exist_yet_uses_its_parent(free_gb, tmp_path):
    free_gb(8)
    assert disk.free_bytes(tmp_path / "not" / "yet" / "made") == 8 * disk.GB
    assert "8.0 GB free" in disk.describe(tmp_path / "not" / "yet")


def test_real_disk_usage_works(tmp_path):
    assert disk.free_bytes(tmp_path) > 0
