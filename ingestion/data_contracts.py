"""Data contracts: check each Freddie Mac file against its contract before it reaches bronze.

A contract (ingestion/contracts/freddie_<kind>_r<release>.yml) lists the columns of a
pipe-delimited file in order, with their types, allowed codes and ranges. `check_file` (a plain
file) or `check_zip` (files inside a zip, streamed, never extracted) reads each file once and
returns errors and warnings; `enforce` raises ContractViolation if there is any error, and the load
stops. There is no "warn only" mode: a broken file must never reach the model.

- errors:   wrong number of columns, an empty key or required field, a value of the wrong type,
            out of range, in the wrong format, or a code not in the list
- warnings: problems in `info` fields (not used by the model); an empty file

Messages name line numbers, never loan values, so logs can be shared without loan data. The one
exception is fields marked `show_values` (by default, fields with a code list): their unexpected
values are category codes such as '06', not loan data, and are quoted so a contract can be fixed.
Pure Python and offline: the unit tests use small synthetic fixture files.
(Pattern from Bank Health's data contracts, adapted to Freddie's headerless pipe files.)
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACT_DIR = Path(__file__).with_name("contracts")
TYPES = ("int", "decimal", "yyyymm", "str")
LEVELS = ("key", "required", "nullable", "info")
EXAMPLES = 3  # line numbers quoted per finding
SHOWN_VALUES = 5  # distinct unexpected code values quoted per finding
YYYYMM = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")
# Freddie's files are plain ASCII; latin-1 never fails to decode, so an odd byte in a seller name
# can't crash the check.
ENCODING = "latin-1"

# Freddie file names -> contract kind. Standard: orig_2007Q1.txt; sample: sample_orig_2007.txt
FILE_KINDS = (
    (re.compile(r"^(sample_)?orig_\d{4}(Q[1-4])?\.txt$"), "orig"),
    (re.compile(r"^(sample_)?perf_\d{4}(Q[1-4])?\.txt$"), "perf"),
)


class ContractViolation(Exception):
    """A source file broke its contract; nothing from it may go to bronze."""


@dataclass(frozen=True)
class FieldRule:
    name: str
    type: str
    level: str
    codes: tuple[str, ...] | None = None
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    not_available: tuple[str, ...] = ()
    show_values: bool = False  # quote unexpected values in messages (category codes only)
    # A required field Freddie occasionally leaves empty: empty in up to this share of rows is a
    # warning (with the count), above it an error. None = never empty.
    max_empty_share: float | None = None
    # Count values above this (allowed, but unusual and worth a decision downstream). None = no flag.
    flag_above: float | None = None


@dataclass(frozen=True)
class Contract:
    source: str
    file_kind: str
    layout: str
    valid_from_release: int
    delimiter: str
    row_id: str
    fields: tuple[FieldRule, ...]
    unique: tuple[str, ...] = ()

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]


@dataclass
class Result:
    source: str
    layout: str
    file: str
    rows: int = 0
    distinct_ids: int = 0  # distinct row_id values (loans), for tie-outs between files
    # For the run log, so changes across releases are visible: empty rows in tolerated fields, and
    # rows above each field's flag threshold (keys like "orig_loan_term>360"). Zero counts included.
    tolerated_empty: dict[str, int] = field(default_factory=dict)
    flagged: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- contracts
def load_contract(path: Path) -> Contract:
    import yaml  # here, not at module level, so importing this module needs no third-party package

    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = []
    for f in spec["fields"]:
        if f["type"] not in TYPES or f["level"] not in LEVELS:
            raise ValueError(f"{path.name}: {f['name']}: unknown type or level")
        rules.append(
            FieldRule(
                name=f["name"],
                type=f["type"],
                level=f["level"],
                codes=tuple(str(c) for c in f["codes"]) if "codes" in f else None,
                pattern=f.get("pattern"),
                min=f.get("min"),
                max=f.get("max"),
                not_available=tuple(str(v) for v in f.get("not_available", ())),
                show_values=bool(f.get("show_values", "codes" in f)),
                max_empty_share=f.get("max_empty_share"),
                flag_above=f.get("flag_above"),
            )
        )
        if rules[-1].max_empty_share is not None and rules[-1].level not in ("key", "required"):
            raise ValueError(f"{path.name}: {f['name']}: max_empty_share only applies to required fields")
    names = [r.name for r in rules]
    if len(set(names)) != len(names):
        raise ValueError(f"{path.name}: duplicate field names")
    for key in [spec["row_id"], *spec.get("unique", [])]:
        if key not in names:
            raise ValueError(f"{path.name}: {key!r} is not a contract field")
    return Contract(
        source=spec["source"],
        file_kind=spec["file_kind"],
        layout=spec["layout"],
        valid_from_release=int(spec["valid_from_release"]),
        delimiter=spec["delimiter"],
        row_id=spec["row_id"],
        fields=tuple(rules),
        unique=tuple(spec.get("unique", [])),
    )


def contract_for(kind: str, release: int, contract_dir: Path = CONTRACT_DIR) -> Contract:
    """The newest contract of this kind whose layout applies to the given Freddie release."""
    candidates = [load_contract(p) for p in sorted(contract_dir.glob(f"freddie_{kind}_r*.yml"))]
    usable = [c for c in candidates if c.valid_from_release <= release]
    if not usable:
        raise ContractViolation(f"no {kind} contract covers Freddie release {release}")
    return max(usable, key=lambda c: c.valid_from_release)


def kind_for(path: Path) -> str:
    for pattern, kind in FILE_KINDS:
        if pattern.match(Path(path).name):
            return kind
    raise ContractViolation(f"{Path(path).name}: not a Freddie origination or performance file name")


# --------------------------------------------------------------------------- checking
def _validator(rule: FieldRule) -> Callable[[str], str | None]:
    """A function that says what is wrong with one non-empty value (or None). Built once per field,
    with its pattern compiled and its code list as a set, because it runs tens of millions of times."""
    na = frozenset(rule.not_available)
    lo, hi = rule.min, rule.max

    if rule.type in ("int", "decimal"):
        parse: Callable[[str], float] = int if rule.type == "int" else float
        kind = "not a whole number" if rule.type == "int" else "not a number"

        def check_number(value: str) -> str | None:
            if value in na:
                return None
            try:
                number = parse(value)
            except ValueError:
                return kind
            if lo is not None and number < lo:
                return f"below {lo:g}"
            if hi is not None and number > hi:
                return f"above {hi:g}"
            return None

        return check_number

    if rule.type == "yyyymm":
        return lambda value: None if value in na or YYYYMM.match(value) else "not a YYYYMM date"

    codes = frozenset(rule.codes) if rule.codes is not None else None
    pattern = re.compile(rule.pattern) if rule.pattern else None

    def check_text(value: str) -> str | None:
        if value in na:
            return None
        if codes is not None and value not in codes:
            return "not an allowed code"
        if pattern is not None and not pattern.search(value):
            return "wrong format"
        return None

    return check_text


def check_lines(lines: Iterable[str], contract: Contract, file_label: str) -> Result:
    res = Result(source=contract.source, layout=contract.layout, file=file_label)
    width = len(contract.fields)
    checks = [
        (i, rule, _validator(rule), rule.level in ("key", "required"))
        for i, rule in enumerate(contract.fields)
    ]
    bad_width: Counter[int] = Counter()
    bad_width_lines: list[int] = []
    # (field, problem) -> count, first line numbers, and (for show_values fields) the values seen
    problems: Counter[tuple[str, str]] = Counter()
    problem_lines: dict[tuple[str, str], list[int]] = {}
    problem_values: dict[tuple[str, str], Counter[str]] = {}
    positions = {name: i for i, name in enumerate(contract.names)}
    unique: list[tuple[str, int, set[str]]] = [(name, positions[name], set()) for name in contract.unique]
    id_pos = positions[contract.row_id]
    ids: set[str] = set()
    delimiter = contract.delimiter
    tolerance = {r.name: r.max_empty_share for r in contract.fields if r.max_empty_share is not None}
    flag_keys = {r.name: f"{r.name}>{r.flag_above:g}" for r in contract.fields if r.flag_above is not None}
    res.tolerated_empty = dict.fromkeys(tolerance, 0)
    res.flagged = dict.fromkeys(flag_keys.values(), 0)

    def note(key: tuple[str, str], line_no: int, value: str | None = None) -> None:
        problems[key] += 1
        lines_for = problem_lines.setdefault(key, [])
        if len(lines_for) < EXAMPLES:
            lines_for.append(line_no)
        if value is not None:
            problem_values.setdefault(key, Counter())[value] += 1

    for line_no, line in enumerate(lines, start=1):
        line = line.rstrip("\r\n")
        if not line:
            continue
        res.rows += 1
        values = line.split(delimiter)
        if len(values) != width:
            bad_width[len(values)] += 1
            if len(bad_width_lines) < EXAMPLES:
                bad_width_lines.append(line_no)
            continue
        for i, rule, validate, must_have in checks:
            value = values[i].strip()
            if not value:
                if must_have:
                    note((rule.name, "empty"), line_no)
                continue
            problem = validate(value)
            if problem:
                note((rule.name, problem), line_no, value if rule.show_values else None)
            elif rule.flag_above is not None and value not in rule.not_available:
                if float(value) > rule.flag_above:
                    res.flagged[flag_keys[rule.name]] += 1
        ids.add(values[id_pos].strip())
        for name, pos, seen in unique:
            value = values[pos].strip()
            if value in seen:
                note((name, "duplicate"), line_no)
            seen.add(value)

    res.distinct_ids = len(ids)
    if res.rows == 0:
        res.warnings.append(f"{file_label}: no rows; nothing to check")
        return res
    if bad_width:
        found = ", ".join(f"{n} columns x{c:,}" for n, c in bad_width.most_common())
        res.errors.append(
            f"{file_label}: expected {width} columns ({contract.layout}), found {found} "
            f"(lines {bad_width_lines}). Has Freddie changed the layout? Add a new contract version."
        )
    levels = {r.name: r.level for r in contract.fields}
    for (name, problem), count in sorted(problems.items()):
        key = (name, problem)
        msg = f"{file_label}: {contract.source}.{name}: {problem} in {count:,} row(s) (lines {problem_lines[key]})"
        if key in problem_values:
            shown = ", ".join(f"{v!r} x{c:,}" for v, c in problem_values[key].most_common(SHOWN_VALUES))
            more = len(problem_values[key]) - SHOWN_VALUES
            msg += f"; values: {shown}" + (f" and {more} more" if more > 0 else "")
        if problem == "empty" and name in tolerance:
            # Freddie leaves this required field empty now and then: a small share is a warning
            # (counted for the run log), a larger share means something changed, so the load stops.
            res.tolerated_empty[name] = count
            share, limit = count / res.rows, tolerance[name]
            msg += f"; {share:.4%} of rows, limit {limit:.2%}"
            (res.warnings if share <= limit else res.errors).append(msg)
            continue
        (res.warnings if levels[name] == "info" else res.errors).append(msg)
    return res


def check_file(path: Path, release: int, contract_dir: Path = CONTRACT_DIR) -> Result:
    """Check one Freddie file (orig_* or perf_*, standard or sample) against the right contract."""
    path = Path(path)
    contract = contract_for(kind_for(path), release, contract_dir)
    with path.open(encoding=ENCODING, newline="") as fh:
        return check_lines(fh, contract, path.name)


def check_zip(zip_path: Path, release: int, contract_dir: Path = CONTRACT_DIR) -> list[Result]:
    """Check every file inside a Freddie zip, streaming each one: nothing is extracted to disk.

    Why stream: the sample zips hold about 8.8 GB of text in total, more than the free disk space on
    the development PC, so unzipping is not an option (ADR-001). A file in the zip that isn't a
    Freddie origination or performance file is reported as an error.
    """
    zip_path = Path(zip_path)
    results = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            label = f"{zip_path.name}:{name}"
            try:
                contract = contract_for(kind_for(Path(name)), release, contract_dir)
            except ContractViolation as exc:
                results.append(Result(source="unknown", layout="-", file=label, errors=[f"{label}: {exc}"]))
                continue
            with zf.open(info) as raw:
                results.append(
                    check_lines(io.TextIOWrapper(raw, encoding=ENCODING, newline=""), contract, label)
                )
    return results


def enforce(results: list[Result]) -> dict[str, Any]:
    """Print every finding and raise ContractViolation if any file has an error."""
    errors = [e for r in results for e in r.errors]
    warnings = [w for r in results for w in r.warnings]
    for r in results:
        print(f"contract {r.source} ({r.layout}) {r.file}: {r.rows:,} rows, {'ok' if r.ok else 'FAILED'}")
    for e in errors:
        print("ERROR " + e)
    for w in warnings:
        print("WARN " + w)
    if errors:
        more = f" (+{len(errors) - 1} more, see the log)" if len(errors) > 1 else ""
        raise ContractViolation(f"data contract broken: {errors[0]}{more}")
    return {"files": len(results), "rows": sum(r.rows for r in results), "warnings": warnings}
