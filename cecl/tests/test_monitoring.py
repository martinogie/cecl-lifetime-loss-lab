"""Model monitoring (cecl/monitoring.py): PSI from stored profiles, freshness, calibration cells, alerts and
the retrain trigger, on made-up numbers."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from cecl import metrics, monitoring


def test_profile_psi_matches_metrics_psi() -> None:
    rng = np.random.default_rng(1)
    base, now = rng.beta(2, 50, 5000), rng.beta(2, 40, 5000)
    prof = monitoring.profile(base)
    assert len(prof["shares"]) == len(prof["edges"]) + 1 and sum(prof["shares"]) == pytest.approx(1.0)
    assert monitoring.psi(prof, now) == pytest.approx(metrics.psi(base, now), rel=1e-9)
    assert monitoring.psi(prof, base) == pytest.approx(0.0, abs=1e-9)


def test_profile_holds_no_scores() -> None:
    prof = monitoring.profile(np.arange(1000.0))
    assert set(prof) == {"edges", "shares", "n", "mean"} and len(prof["edges"]) <= 9
    with pytest.raises(ValueError):
        monitoring.profile([])


def test_psi_status() -> None:
    assert monitoring.psi_status(0.05) == "green"
    assert monitoring.psi_status(0.2) == "amber"
    assert monitoring.psi_status(0.3) == "red"


def test_freshness_and_weekly_status() -> None:
    today = date(2026, 11, 25)
    latest = {"freddie_performance": "202606", "fred_national": "202610", "fred_state_unemployment": "202609",
              "fhfa_hpi": "202607", "ffiec_call_reports": None}  # fmt: skip
    f = monitoring.freshness(latest, today)
    assert f["freddie_performance"]["status"] == "green" and f["freddie_performance"]["months_old"] == 5
    assert f["fhfa_hpi"]["status"] == "amber"  # 4 months old: one month late
    assert f["ffiec_call_reports"]["status"] == "red"  # missing
    s = monitoring.weekly_status(f, None)
    assert s["status"] == "red" and not s["scored"]
    ok = {k: {"status": "green"} for k in monitoring.FRESHNESS}
    assert monitoring.weekly_status(ok, {"current": 0.12})["status"] == "amber"


def test_needs_scoring() -> None:
    assert monitoring.needs_scoring("202610", None)
    assert monitoring.needs_scoring("202611", "202610")
    assert not monitoring.needs_scoring("202610", "202610")
    assert not monitoring.needs_scoring(None, "202610")


def test_cells_summary_and_alerts() -> None:
    groups = ["2026"] * 1000 + ["2025"] * 1000
    event = [True] * 300 + [False] * 700 + [True] * 50 + [False] * 950
    prob = [0.2] * 1000 + [0.05] * 1000  # 2026: 200 predicted vs 300 actual (0.67, judged); 2025: 50 vs 50
    cs = monitoring.cells("current->default", "year", groups, event, prob)
    by = {c.key: c for c in cs}
    assert by["2026"].judged and not by["2026"].passes and by["2026"].ratio == pytest.approx(200 / 300)
    assert not by["2025"].judged and by["2025"].passes
    pay = monitoring.cells("current->prepaid", "year", groups, event, prob)
    s = monitoring.quarterly_summary("202609", cs + pay, {"current->default": 0.81}, {"current": 0.04})
    assert s["default_cells_failed"] == 1 and len(s["failed_cells"]) == 2
    lines = monitoring.alerts(s)
    assert len(lines) == 2 and "0.67" in lines[0] and "outside 0.80-1.25" in lines[0]


def test_retrain_due() -> None:
    trained = date(2026, 11, 18)
    calm = {"default_cells_failed": 0, "psi": {"current": 0.05}}
    assert not monitoring.retrain_due(trained, date(2027, 5, 1), [calm | {"quarter": "202609"}])["due"]
    assert monitoring.retrain_due(trained, date(2027, 11, 18), [])["due"]  # yearly
    one_bad = [calm | {"quarter": "202609"}, {"quarter": "202612", "default_cells_failed": 2, "psi": {}}]
    assert not monitoring.retrain_due(trained, date(2027, 4, 1), one_bad)["due"]
    two_bad = [
        {"quarter": "202609", "default_cells_failed": 1},
        {"quarter": "202612", "default_cells_failed": 3},
    ]
    r = monitoring.retrain_due(trained, date(2027, 4, 1), two_bad)
    assert r["due"] and "202609 and 202612" in r["reasons"][0]
    psi_two = [
        {"quarter": "202609", "psi": {"30": 0.3, "60": 0.3}},
        {"quarter": "202612", "psi": {"30": 0.26}},
    ]
    r = monitoring.retrain_due(trained, date(2027, 4, 1), psi_two)
    assert r["due"] and r["reasons"] == ["PSI above 0.25 for 30 in 202609 and 202612"]
