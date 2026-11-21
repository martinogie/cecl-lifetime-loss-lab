"""The prod model copy (VM cutover): names, tags, and its refusals."""

from __future__ import annotations

import pytest

from scripts import copy_models_to_prod as cm


def test_names_map_dev_to_prod() -> None:
    assert cm.names("30", "cecl_lab") == (
        "cecl_lab_dev.ml.pd_v2_transition_30",
        "cecl_lab.ml.pd_v2_transition_30",
    )


def test_same_catalog_and_unknown_step_refused() -> None:
    with pytest.raises(ValueError):
        cm.names("30", "cecl_lab_dev")
    with pytest.raises(ValueError):
        cm.names("90", "cecl_lab")


def test_tags_keep_dev_tags_and_say_where_from() -> None:
    tags = cm.copy_tags(
        {"model_version": "v2", "holdout": "failed"}, "cecl_lab_dev.ml.pd_v2_transition_60", "1"
    )
    assert tags == {
        "model_version": "v2",
        "holdout": "failed",
        "copied_from": "cecl_lab_dev.ml.pd_v2_transition_60 v1",
    }


def test_refuses_outside_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CECL_ENV", "dev")
    with pytest.raises(SystemExit, match="only with CECL_ENV=prod"):
        cm.main([])
