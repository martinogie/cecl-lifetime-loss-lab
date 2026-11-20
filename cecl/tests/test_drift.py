"""Simulated-history drift diagnosis: the recorder changes nothing and lines up with the real rows."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from cecl import features as fx  # noqa: E402
from cecl.models import drift, simulate  # noqa: E402
from cecl.tests.test_transitions import MATRIX, macro  # noqa: E402
from cecl.tests.test_v2 import Recorder, v2_start  # noqa: E402


def test_recorder_changes_nothing_and_summarises_paths() -> None:
    models = {s: Recorder(r) for s, r in MATRIX.items()}
    start, history = v2_start([fx.CURRENT] * 30 + [fx.LATE_30] * 10)
    plain = simulate.project(models, start, history, macro(), "201701", 6, paths=4, seed=3)
    rec = drift.SimRecorder(paths=4)
    seen = simulate.project(models, start, history, macro(), "201701", 6, paths=4, seed=3, observer=rec)
    assert np.array_equal(plain.p_default, seen.p_default)
    assert rec.rows[0]["alive"] == pytest.approx(40) and rec.rows[0]["share_30"] == pytest.approx(0.25)
    assert rec.rows[0]["roll_current_to_30"] == pytest.approx(MATRIX[fx.CURRENT][fx.LATE_30])
    assert rec.rows[3]["alive"] < 40  # paths default or prepay over time


def test_actual_rows_and_comparison() -> None:
    rows = pd.DataFrame({"period_ym": ["201701"] * 4, "state_prev": [0, 0, 0, 1], "state": [0, 1, 0, 2],
                         "weight": [25.0, 1.0, 25.0, 1.0], "late_months_12": [0, 1, 2, 1],
                         "worst_state_6": [0, 0, 1, 1], "months_since_first_miss": [0, 3, 5, 1],
                         "state_last_month": [0, 0, 1, 1]})  # fmt: skip
    a = drift.actual_by_month(rows, "201701", 2)
    assert list(a) == [0] and a[0]["alive"] == 52.0
    assert a[0]["roll_current_to_30"] == pytest.approx(1 / 51) and a[0]["roll_30_to_60"] == 1.0
    sim = {0: dict.fromkeys(drift.STATS, 1.0)}
    table = drift.compare(sim, a)
    assert table[0]["alive"]["ratio"] == pytest.approx(1 / 52)
    assert drift.by_year(table)["year_1"]["alive"] == pytest.approx(1 / 52)
