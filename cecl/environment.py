"""Which environment this process runs in, and what that environment may touch.

Every component reads CECL_ENV through this module: Airflow tasks, the loaders, dbt (profiles.yml
picks its target from it), the Databricks notebooks (their `env` parameter) and the site export.
There is no default. If CECL_ENV is missing or not one of the names below, everything stops with a
clear error instead of guessing. (Same pattern as Bank Health's BANK_HEALTH_ENV.)

| env   | runs where                  | Databricks catalog | dbt target | may write prod, publish, move @champion |
|-------|-----------------------------|--------------------|------------|------------------------------------------|
| prod  | its own Airflow instance    | cecl_lab           | prod       | yes                                      |
| dev   | Docker on the PC, port 8082 | cecl_lab_dev       | dev        | no                                       |
| ci    | GitHub Actions              | none               | local      | no                                       |
| local | a laptop, DuckDB only       | none               | local      | no                                       |

Why this lives in `cecl` and not `ingestion`: the Databricks notebooks only get the `cecl` wheel,
and they must run the same checks as Airflow.

Databricks layout. The plan is one catalog per environment. If a workspace does not allow a new
catalog, the fallback (CECL_DATABRICKS_LAYOUT=schema_prefix) keeps both environments in the
workspace's default catalog and separates them by schema name instead:

    catalogs       cecl_lab_dev.bronze              cecl_lab.bronze
    schema_prefix  workspace.cecl_lab_dev_bronze    workspace.cecl_lab_bronze
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_VAR = "CECL_ENV"
ENVIRONMENTS = ("local", "ci", "dev", "prod")
AIRFLOW_ENVIRONMENTS = ("dev", "prod")  # an Airflow stack is always one of these

LAYOUT_VAR = "CECL_DATABRICKS_LAYOUT"
LAYOUTS = ("catalogs", "schema_prefix")
DEFAULT_LAYOUT = "catalogs"  # the plan; the fallback is chosen explicitly (ADR-004)
FALLBACK_CATALOG = "workspace"  # Free Edition's default catalog; used only by schema_prefix

PROD_CATALOG = "cecl_lab"
DEV_CATALOG = "cecl_lab_dev"
LAYERS = ("bronze", "silver", "gold", "ml", "ops")

# Model registry aliases. A newly trained model is registered as @challenger; it becomes
# @champion only after a person reviews it, and only prod moves @champion.
CHALLENGER = "challenger"
CHAMPION = "champion"


class EnvironmentNotSet(RuntimeError):
    """CECL_ENV is missing or not one of the allowed names."""


class EnvironmentMismatch(RuntimeError):
    """A setting belongs to a different environment than CECL_ENV, or is not available in it."""


class ProdWriteRefused(PermissionError):
    """A non-prod environment tried to write a prod object, publish, or move @champion."""


@dataclass(frozen=True)
class Settings:
    name: str
    databricks_catalog: str | None  # the catalog in the "catalogs" layout
    dbt_target: str
    mlflow_experiment: str | None  # folder name; the full path is under the user's workspace folder

    @property
    def is_prod(self) -> bool:
        return self.name == "prod"


SETTINGS = {
    "prod": Settings("prod", PROD_CATALOG, "prod", "cecl-lifetime-loss-lab"),
    "dev": Settings("dev", DEV_CATALOG, "dev", "cecl-lifetime-loss-lab-dev"),
    "ci": Settings("ci", None, "local", None),
    "local": Settings("local", None, "local", None),
}


def current_env() -> str:
    """local, ci, dev or prod, from CECL_ENV. Raises EnvironmentNotSet otherwise (no default)."""
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        raise EnvironmentNotSet(
            f"{ENV_VAR} is not set. Set it to one of {', '.join(ENVIRONMENTS)} "
            "(in .env for scripts, in the Compose file for Airflow, in the workflow for CI). "
            "There is deliberately no default."
        )
    env = raw.strip()
    if env not in ENVIRONMENTS:
        raise EnvironmentNotSet(
            f"{ENV_VAR}={raw!r} is not valid. Use exactly one of {', '.join(ENVIRONMENTS)}."
        )
    return env


def settings() -> Settings:
    return SETTINGS[current_env()]


def require_airflow_env() -> Settings:
    """For Airflow DAGs and tasks: CECL_ENV must be dev or prod. Called at DAG import (so a missing
    value shows as a broken DAG in the UI) and again at the start of every task."""
    s = settings()
    if s.name not in AIRFLOW_ENVIRONMENTS:
        raise EnvironmentMismatch(
            f"{ENV_VAR}={s.name}: Airflow runs only as {' or '.join(AIRFLOW_ENVIRONMENTS)}. "
            "Check the Compose file of this stack."
        )
    return s


def layout() -> str:
    raw = os.environ.get(LAYOUT_VAR, "").strip() or DEFAULT_LAYOUT
    if raw not in LAYOUTS:
        raise EnvironmentMismatch(f"{LAYOUT_VAR}={raw!r} is not valid. Use one of {', '.join(LAYOUTS)}.")
    return raw


def databricks_schema(layer: str) -> str:
    """Full `catalog.schema` for a layer (bronze, silver, gold, ml, ops) in the current environment."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; use one of {', '.join(LAYERS)}")
    s = settings()
    if s.databricks_catalog is None:
        raise EnvironmentMismatch(f"{ENV_VAR}={s.name} has no Databricks objects")
    if layout() == "catalogs":
        return f"{s.databricks_catalog}.{layer}"
    return f"{FALLBACK_CATALOG}.{s.databricks_catalog}_{layer}"


def registered_model(name: str) -> str:
    """Unity Catalog name of a model in this environment, e.g. cecl_lab.ml.pd_hazard."""
    return f"{databricks_schema('ml')}.{name}"


def mlflow_experiment_path(user: str) -> str:
    """Workspace path of this environment's MLflow experiment (prod and dev are separate)."""
    s = settings()
    if s.mlflow_experiment is None:
        raise EnvironmentMismatch(f"{ENV_VAR}={s.name} has no MLflow experiment")
    return f"/Users/{user}/{s.mlflow_experiment}"


def is_prod_object(name: str) -> bool:
    """True for anything in prod, in either layout.

    Accepts cecl_lab, cecl_lab.gold.x, /Volumes/cecl_lab/bronze/raw, workspace.cecl_lab_gold.x.
    Matching is on whole name parts, so cecl_lab_dev and workspace.cecl_lab_dev_gold are not prod.
    """
    text = name.strip()
    if text.startswith("/Volumes/"):
        text = text[len("/Volumes/") :].replace("/", ".")
    parts = [p.strip().strip("`").lower() for p in text.split(".")]
    if parts[0] == PROD_CATALOG:
        return True
    if parts[0] == FALLBACK_CATALOG and len(parts) > 1:
        return any(parts[1] == f"{PROD_CATALOG}_{layer}" for layer in LAYERS)
    return False


def assert_can_write(target: str) -> None:
    """Raise ProdWriteRefused if a non-prod environment is about to write a prod object."""
    env = current_env()
    if env != "prod" and is_prod_object(target):
        raise ProdWriteRefused(f"{ENV_VAR}={env} may not write {target}: only prod writes prod objects.")


def assert_can_publish(what: str) -> None:
    """Only prod publishes (site data to R2, the allowance memo's published figures)."""
    env = current_env()
    if env != "prod":
        raise ProdWriteRefused(f"{ENV_VAR}={env} may not publish {what}: only prod publishes.")


def assert_can_set_alias(model: str, alias: str) -> None:
    """@challenger may be set in dev or prod; only prod moves @champion."""
    if alias not in (CHALLENGER, CHAMPION):
        raise ValueError(f"unknown alias {alias!r}; use {CHALLENGER} or {CHAMPION}")
    env = current_env()
    if env not in AIRFLOW_ENVIRONMENTS:
        raise ProdWriteRefused(f"{ENV_VAR}={env} has no model registry.")
    if alias == CHAMPION and env != "prod":
        raise ProdWriteRefused(f"{ENV_VAR}={env} may not move {model}@{CHAMPION}: only prod promotes.")


def describe() -> str:
    """One line for logs. Names only, never credentials."""
    s = settings()
    catalog = s.databricks_catalog or "none"
    return f"environment={s.name} databricks_catalog={catalog} layout={layout()} dbt_target={s.dbt_target}"
