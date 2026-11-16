"""Every bronze table that something reads is declared, on both dbt targets (step 9 follow-up).

Why: dbt's `dev`/`prod` targets read bronze through `source('bronze', ...)`, but the local and CI
targets read Parquet folders or seeds and never touch the sources, so an undeclared source only
fails on Databricks. That is how `bronze.freddie_performance` stayed missing from Phase 0 to
step 9. These checks run in CI with no Databricks access:

- Databricks targets: every table the dbt models reach (via the `freddie_relation` macro or a
  direct `source()` call), the bronze notebook writes, and the tie-outs read is declared in
  `dbt/models/staging/_sources.yml`.
- Local target: the macro's Parquet folders are exactly the folders the bronze loader writes and
  the tie-outs read.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ingestion import data_contracts, freddie, tie_outs

ROOT = Path(__file__).resolve().parents[2]
DBT = ROOT / "dbt"
MACRO = DBT / "macros" / "freddie_relation.sql"
FOLDERS = re.compile(r"set folder = (\{[^}]*\})")
RELATION_CALL = re.compile(r"freddie_relation\(\s*'(\w+)'\s*\)")
SOURCE_CALL = re.compile(r"source\(\s*'(\w+)'\s*,\s*'(\w+)'\s*\)")


def declared_bronze() -> set[str]:
    doc = yaml.safe_load((DBT / "models" / "staging" / "_sources.yml").read_text(encoding="utf-8"))
    return {t["name"] for s in doc["sources"] if s["name"] == "bronze" for t in s["tables"]}


def macro_folders() -> dict[str, str]:
    """The macro's kind -> local Parquet folder map, e.g. {'origination': 'freddie_orig', ...}."""
    match = FOLDERS.search(MACRO.read_text(encoding="utf-8"))
    assert match, "freddie_relation.sql no longer has its folder map; update this test"
    pairs = re.findall(r"'(\w+)'\s*:\s*'(\w+)'", match.group(1))
    return dict(pairs)


def model_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (DBT / "models").rglob("*.sql"))


def test_every_source_the_dbt_models_reach_is_declared():
    declared, folders = declared_bronze(), macro_folders()
    # The macro turns kind K into source('bronze', 'freddie_' ~ K) on Databricks.
    assert "source('bronze', 'freddie_' ~ kind)" in MACRO.read_text(encoding="utf-8")
    used_kinds = set(RELATION_CALL.findall(model_sql()))
    assert used_kinds, "no model calls freddie_relation(); update this test"
    assert used_kinds <= set(folders), f"kinds without a local folder: {used_kinds - set(folders)}"
    missing = {f"freddie_{k}" for k in used_kinds} - declared
    direct = {t for s, t in SOURCE_CALL.findall(model_sql()) if s == "bronze"}
    missing |= direct - declared
    assert not missing, f"declare these in dbt/models/staging/_sources.yml: {sorted(missing)}"


def test_tables_the_databricks_code_writes_and_reads_are_declared():
    declared = declared_bronze()
    rel = tie_outs.databricks_relations(lambda layer: f"cat.{layer}")
    read = {rel["bronze_orig"].rsplit(".", 1)[1], rel["bronze_perf"].rsplit(".", 1)[1]}
    notebook = (ROOT / "databricks" / "notebooks" / "10_bronze_freddie.py").read_text(encoding="utf-8")
    written = set(re.findall(r"\{BRONZE\}\.(freddie_\w+)", notebook))
    assert written == read, f"the bronze notebook writes {written} but the tie-outs read {read}"
    assert written <= declared, f"declare these in _sources.yml: {sorted(written - declared)}"


def test_local_folders_match_the_loader_and_the_tie_outs():
    folders = set(macro_folders().values())
    # The loader names its output folder freddie_<kind> from the contract's file kinds.
    written = {
        freddie.output_path(Path("root"), kind, f"sample_{kind}_2007.txt").parent.parent.name
        for _, kind in data_contracts.FILE_KINDS
    }
    assert folders == written, f"dbt reads {folders}, the bronze loader writes {written}"
    local = tie_outs.local_relations(Path("b"), Path("p"))
    assert {f for f in folders if f in local["bronze_orig"] + local["bronze_perf"]} == folders
