from __future__ import annotations

import pytest

from cecl import environment as env


@pytest.fixture
def as_env(monkeypatch):
    def _set(name: str | None, layout: str | None = None) -> None:
        if name is None:
            monkeypatch.delenv(env.ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(env.ENV_VAR, name)
        if layout is None:
            monkeypatch.delenv(env.LAYOUT_VAR, raising=False)
        else:
            monkeypatch.setenv(env.LAYOUT_VAR, layout)

    return _set


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_env_is_refused(as_env, value):
    as_env(value)
    with pytest.raises(env.EnvironmentNotSet, match="deliberately no default"):
        env.current_env()


@pytest.mark.parametrize("value", ["Prod", "production", "staging"])
def test_unknown_env_is_refused(as_env, value):
    as_env(value)
    with pytest.raises(env.EnvironmentNotSet, match="not valid"):
        env.current_env()


@pytest.mark.parametrize("name", env.ENVIRONMENTS)
def test_each_env_has_settings(as_env, name):
    as_env(f" {name} ")
    assert env.settings().name == name
    assert env.settings().is_prod is (name == "prod")


@pytest.mark.parametrize(
    ("name", "target"), [("local", "local"), ("ci", "local"), ("dev", "dev"), ("prod", "prod")]
)
def test_dbt_target_follows_env(as_env, name, target):
    as_env(name)
    assert env.settings().dbt_target == target


@pytest.mark.parametrize("name", ["dev", "prod"])
def test_airflow_accepts_dev_and_prod(as_env, name):
    as_env(name)
    assert env.require_airflow_env().name == name


@pytest.mark.parametrize("name", ["local", "ci"])
def test_airflow_refuses_local_and_ci(as_env, name):
    as_env(name)
    with pytest.raises(env.EnvironmentMismatch, match="Airflow runs only as dev or prod"):
        env.require_airflow_env()


def test_airflow_refuses_missing_env(as_env):
    as_env(None)
    with pytest.raises(env.EnvironmentNotSet):
        env.require_airflow_env()


@pytest.mark.parametrize(
    ("name", "layout", "expected"),
    [
        ("prod", None, "cecl_lab.gold"),
        ("dev", None, "cecl_lab_dev.gold"),
        ("prod", "catalogs", "cecl_lab.gold"),
        ("prod", "schema_prefix", "workspace.cecl_lab_gold"),
        ("dev", "schema_prefix", "workspace.cecl_lab_dev_gold"),
    ],
)
def test_databricks_schema_per_layout(as_env, name, layout, expected):
    as_env(name, layout)
    assert env.databricks_schema("gold") == expected


def test_unknown_layout_is_refused(as_env):
    as_env("dev", "one_big_schema")
    with pytest.raises(env.EnvironmentMismatch, match="CECL_DATABRICKS_LAYOUT"):
        env.layout()


def test_unknown_layer_is_refused(as_env):
    as_env("dev")
    with pytest.raises(ValueError, match="unknown layer"):
        env.databricks_schema("platinum")


@pytest.mark.parametrize("name", ["local", "ci"])
def test_local_and_ci_have_no_databricks(as_env, name):
    as_env(name)
    with pytest.raises(env.EnvironmentMismatch, match="no Databricks"):
        env.databricks_schema("bronze")
    with pytest.raises(env.EnvironmentMismatch, match="no MLflow"):
        env.mlflow_experiment_path("someone")


def test_registered_model_and_experiments_are_separate(as_env):
    as_env("prod")
    prod_model, prod_exp = env.registered_model("pd_hazard"), env.mlflow_experiment_path("me")
    as_env("dev")
    dev_model, dev_exp = env.registered_model("pd_hazard"), env.mlflow_experiment_path("me")
    assert prod_model == "cecl_lab.ml.pd_hazard"
    assert dev_model == "cecl_lab_dev.ml.pd_hazard"
    assert prod_exp == "/Users/me/cecl-lifetime-loss-lab"
    assert dev_exp == "/Users/me/cecl-lifetime-loss-lab-dev"


@pytest.mark.parametrize(
    ("name", "is_prod"),
    [
        ("cecl_lab", True),
        ("cecl_lab.gold.allowance", True),
        ("`cecl_lab`.gold", True),
        ("/Volumes/cecl_lab/bronze/raw/x.txt", True),
        ("workspace.cecl_lab_gold.allowance", True),
        ("cecl_lab_dev", False),
        ("cecl_lab_dev.gold.allowance", False),
        ("/Volumes/cecl_lab_dev/bronze/raw", False),
        ("workspace.cecl_lab_dev_gold.x", False),
        ("workspace.other_schema", False),
        ("workspace", False),
        ("bank_health.gold.x", False),
    ],
)
def test_is_prod_object(name, is_prod):
    assert env.is_prod_object(name) is is_prod


@pytest.mark.parametrize("name", ["local", "ci", "dev"])
def test_non_prod_cannot_write_prod(as_env, name):
    as_env(name)
    with pytest.raises(env.ProdWriteRefused):
        env.assert_can_write("cecl_lab.gold.allowance")
    env.assert_can_write("cecl_lab_dev.gold.allowance")  # its own objects are fine


def test_prod_can_write_prod(as_env):
    as_env("prod")
    env.assert_can_write("cecl_lab.gold.allowance")


@pytest.mark.parametrize("name", ["local", "ci", "dev"])
def test_only_prod_publishes(as_env, name):
    as_env(name)
    with pytest.raises(env.ProdWriteRefused, match="only prod publishes"):
        env.assert_can_publish("site data")


def test_prod_publishes(as_env):
    as_env("prod")
    env.assert_can_publish("site data")


def test_aliases(as_env):
    as_env("dev")
    env.assert_can_set_alias("pd_hazard", env.CHALLENGER)
    with pytest.raises(env.ProdWriteRefused, match="only prod promotes"):
        env.assert_can_set_alias("pd_hazard", env.CHAMPION)
    as_env("prod")
    env.assert_can_set_alias("pd_hazard", env.CHAMPION)
    env.assert_can_set_alias("pd_hazard", env.CHALLENGER)
    with pytest.raises(ValueError, match="unknown alias"):
        env.assert_can_set_alias("pd_hazard", "candidate")
    as_env("local")
    with pytest.raises(env.ProdWriteRefused, match="no model registry"):
        env.assert_can_set_alias("pd_hazard", env.CHALLENGER)


def test_describe_has_names_only(as_env):
    as_env("dev", "schema_prefix")
    assert (
        env.describe()
        == "environment=dev databricks_catalog=cecl_lab_dev layout=schema_prefix dbt_target=dev"
    )
    as_env("ci")
    assert "databricks_catalog=none" in env.describe()
