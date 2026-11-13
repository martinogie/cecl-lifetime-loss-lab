"""Repository checks for a public repo (CLAUDE.md hard rules 1 to 3).

1. .gitignore keeps secrets and every kind of data file out of git, while still allowing the
   synthetic fixtures and dependency pins.
2. No text file contains an account identifier: an email address (other than a GitHub noreply
   address), a Databricks workspace host, an IP address or an Oracle Cloud OCID.

gitleaks (CI and pre-commit) looks for secrets; this test looks for identifiers, which gitleaks
does not flag.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SKIP_DIRS = {
    ".git",
    "data",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "target",
    "dbt_packages",
    "logs",
    ".databricks",
    "htmlcov",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".csv", ".sql", ".sh", ".cfg", ".ini", ".json",
    ".example", ".html", ".css", ".js", "",
}  # fmt: skip

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_EMAIL_ENDINGS = ("@users.noreply.github.com", "@example.com")
IDENTIFIERS = {
    "Databricks workspace host": re.compile(
        r"[A-Za-z0-9-]+\.(?:cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)|\bdbc-[0-9a-f]{4,}"
    ),
    "IP address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "Oracle Cloud OCID": re.compile(r"\bocid1\.[a-z0-9]+\.oc\d"),
}
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0"}


def repo_text_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith("."):
            files.append(path)
    return files


def test_there_are_files_to_scan():
    names = {p.name for p in repo_text_files()}
    assert {"pyproject.toml", ".gitignore", "README.md"} <= names


def test_no_account_identifiers_in_text_files():
    findings = []
    for path in repo_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        for match in EMAIL.finditer(text):
            if not match.group(0).lower().endswith(ALLOWED_EMAIL_ENDINGS):
                findings.append(f"{rel}: email address")
        for label, pattern in IDENTIFIERS.items():
            for match in pattern.finditer(text):
                if label == "IP address" and match.group(0) in ALLOWED_IPS:
                    continue
                findings.append(f"{rel}: {label}")
    # Only file names and the kind of finding are printed, never the value itself.
    assert not findings, "replace with a placeholder or an environment variable:\n" + "\n".join(
        sorted(set(findings))
    )


REQUIRED_IGNORES = [
    ".env",
    "keys/",
    "*.p8",
    "*.pem",
    "data/",
    "*.parquet",
    "*.duckdb",
    "*.zip",
    "*.txt",
    "*.csv",
]


def test_gitignore_lists_the_required_patterns():
    lines = {line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    missing = [p for p in REQUIRED_IGNORES if p not in lines]
    assert not missing, f".gitignore must contain {missing}"


def is_ignored(path: str) -> bool:
    """Ask git itself (the rules are subtle: later lines and ! exceptions win)."""
    result = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT, check=False)
    return result.returncode == 0


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        (".env", True),
        (".env.dev", True),
        (".env.example", False),
        ("keys/databricks.pem", True),
        ("deploy/key.p8", True),
        ("data/raw/orig_2007Q1.txt", True),
        ("data/anything/at/all.json", True),
        ("exports/panel.parquet", True),
        ("cecl_lab.duckdb", True),
        ("historical_data_2007Q1.zip", True),
        ("sample_orig_2007.txt", True),
        ("notebooks/scratch.csv", True),
        ("ingestion/tests/fixtures/sample_orig_2023.txt", False),
        ("dbt/tests/fixtures/fixture_freddie_origination.csv", False),
        # The roadmap PDFs are private (prepared for Martins only): never committed.
        ("docs/CECL_Lifetime_Loss_Lab_Roadmap.pdf", True),
        ("docs/CECL_Lifetime_Loss_Lab_Roadmap_v2.pdf", True),
        ("requirements-dev.txt", False),
        ("airflow/requirements.txt", False),
    ],
)
def test_gitignore_behaviour(path, ignored):
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert is_ignored(path) is ignored
