from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from ingestion import data_contracts as dc

FIXTURES = Path(__file__).with_name("fixtures")
ORIG = FIXTURES / "sample_orig_2023.txt"
PERF = FIXTURES / "sample_perf_2023.txt"
RELEASE = 48


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="ascii").splitlines()


def set_field(line: str, contract: dc.Contract, name: str, value: str) -> str:
    values = line.split("|")
    values[contract.names.index(name)] = value
    return "|".join(values)


@pytest.fixture(scope="module")
def orig_contract() -> dc.Contract:
    return dc.contract_for("orig", RELEASE)


@pytest.fixture(scope="module")
def perf_contract() -> dc.Contract:
    return dc.contract_for("perf", RELEASE)


# --------------------------------------------------------------------------- the contracts themselves
def test_contract_widths_match_release_48(orig_contract, perf_contract):
    assert len(orig_contract.fields) == 31
    assert len(perf_contract.fields) == 35
    assert orig_contract.names[0] == "credit_score" and orig_contract.names[19] == "loan_id"
    assert perf_contract.names[:2] == ["loan_id", "period"]
    assert perf_contract.names[-1] == "bankruptcy_cramdown_costs"


def test_default_drivers_have_strict_code_lists(perf_contract):
    rules = {r.name: r for r in perf_contract.fields}
    assert rules["zero_balance_code"].codes == ("01", "02", "03", "09", "15", "16", "96")
    assert rules["borrower_assistance_status"].codes == ("F", "R", "T")
    assert rules["delinquency_due_to_disaster"].codes == ("Y",)
    assert rules["delinquency_status"].pattern is not None


def test_no_contract_for_an_older_release():
    with pytest.raises(dc.ContractViolation, match="no orig contract covers Freddie release 47"):
        dc.contract_for("orig", 47)


def test_newest_applicable_contract_is_chosen(tmp_path):
    for src in dc.CONTRACT_DIR.glob("freddie_orig_r48.yml"):
        (tmp_path / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        newer = src.read_text(encoding="utf-8").replace("valid_from_release: 48", "valid_from_release: 52")
        (tmp_path / "freddie_orig_r52.yml").write_text(
            newer.replace("release_48", "release_52"), encoding="utf-8"
        )
    assert dc.contract_for("orig", 51, tmp_path).layout == "release_48"
    assert dc.contract_for("orig", 53, tmp_path).layout == "release_52"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            ("type: int, level: required, min: 300", "type: integer, level: required, min: 300"),
            "unknown type",
        ),
        (("row_id: loan_id", "row_id: loan_number"), "not a contract field"),
        (("name: msa,", "name: credit_score,"), "duplicate field names"),
    ],
)
def test_broken_contract_files_are_rejected(tmp_path, change, message):
    text = (dc.CONTRACT_DIR / "freddie_orig_r48.yml").read_text(encoding="utf-8").replace(*change)
    path = tmp_path / "freddie_orig_r48.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        dc.load_contract(path)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("orig_2007Q1.txt", "orig"),
        ("perf_2007Q1.txt", "perf"),
        ("sample_orig_2007.txt", "orig"),
        ("sample_perf_2007.txt", "perf"),
    ],
)
def test_kind_from_file_name(name, kind):
    assert dc.kind_for(Path(name)) == kind


@pytest.mark.parametrize("name", ["historical_data_2007Q1.zip", "orig_2007Q5.txt", "notes.txt"])
def test_unknown_file_names_are_refused(name):
    with pytest.raises(dc.ContractViolation, match="not a Freddie"):
        dc.kind_for(Path(name))


# --------------------------------------------------------------------------- good files pass
def test_good_origination_file_passes():
    res = dc.check_file(ORIG, RELEASE)
    assert res.ok, res.errors
    assert res.rows == 3
    assert res.warnings == []


def test_good_performance_file_passes():
    res = dc.check_file(PERF, RELEASE)
    assert res.ok, res.errors
    assert res.rows == 6


def test_enforce_passes_good_files(capsys):
    summary = dc.enforce([dc.check_file(ORIG, RELEASE), dc.check_file(PERF, RELEASE)])
    assert summary == {"files": 2, "rows": 9, "warnings": []}
    assert "ok" in capsys.readouterr().out


def test_blank_lines_and_windows_line_endings_are_fine(orig_contract):
    rows = [line + "\r\n" for line in lines(ORIG)] + ["\n"]
    res = dc.check_lines(rows, orig_contract, "x")
    assert res.ok and res.rows == 3


def test_empty_file_warns(orig_contract):
    res = dc.check_lines([], orig_contract, "empty.txt")
    assert res.ok
    assert res.warnings == ["empty.txt: no rows; nothing to check"]


# --------------------------------------------------------------------------- broken files fail
def test_missing_column_fails(orig_contract):
    rows = lines(ORIG)
    rows[1] = rows[1].rsplit("|", 1)[0]  # drop the last column
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    assert not res.ok
    assert "expected 31 columns (release_48), found 30 columns x1 (lines [2])" in res.errors[0]


def test_extra_column_fails(perf_contract):
    rows = [line + "|NEW" for line in lines(PERF)]
    res = dc.check_lines(rows, perf_contract, "perf.txt")
    assert "found 36 columns x6" in res.errors[0]


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        ("loan_id", "", "empty"),
        ("loan_id", "X23Q49999901", "wrong format"),
        ("credit_score", "", "empty"),
        ("credit_score", "abc", "not a whole number"),
        ("credit_score", "250", "below 300"),
        ("credit_score", "851", "above 850"),
        ("orig_upb", "lots", "not a number"),
        ("first_payment_date", "2024-02", "not a YYYYMM date"),
        ("first_payment_date", "202413", "not a YYYYMM date"),
        ("property_state", "Texas", "wrong format"),
    ],
)
def test_bad_values_fail(orig_contract, name, value, problem):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, name, value)
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    # no values quoted: these fields are not category codes
    assert res.errors == [f"orig.txt: freddie_origination.{name}: {problem} in 1 row(s) (lines [1])"]


def test_unexpected_codes_are_quoted(orig_contract):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "occupancy_status", "X")
    rows[1] = set_field(rows[1], orig_contract, "occupancy_status", "X")
    rows[2] = set_field(rows[2], orig_contract, "occupancy_status", "Z")
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    assert res.errors == [
        "orig.txt: freddie_origination.occupancy_status: not an allowed code in 3 row(s) "
        "(lines [1, 2, 3]); values: 'X' x2, 'Z' x1"
    ]


def test_quoted_values_are_capped(perf_contract):
    rows = []
    base = lines(PERF)[0]
    for code in ["A1", "B1", "C1", "D1", "E1", "F1", "G1"]:
        rows.append(set_field(base, perf_contract, "delinquency_status", code))
    res = dc.check_lines(rows, perf_contract, "perf.txt")
    assert res.errors[0].endswith("and 2 more")
    assert "wrong format in 7 row(s)" in res.errors[0]  # delinquency_status is a pattern with show_values


def test_identifier_values_are_never_quoted(orig_contract):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "loan_id", "SECRET12345")
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    assert "SECRET" not in " ".join(res.errors)


def test_distinct_loans_are_counted():
    assert dc.check_file(ORIG, RELEASE).distinct_ids == 3
    assert dc.check_file(PERF, RELEASE).distinct_ids == 3  # 6 monthly rows, 3 loans


# --------------------------------------------------------------------------- tolerated empties and flags
def perf_rows_with_empty_remaining(perf_contract, total: int, empty: int) -> list[str]:
    base = lines(PERF)[0]
    blank = set_field(base, perf_contract, "remaining_months", "")
    return [blank] * empty + [base] * (total - empty)


def test_remaining_months_rarely_empty_is_a_warning(perf_contract):
    res = dc.check_lines(perf_rows_with_empty_remaining(perf_contract, 2000, 1), perf_contract, "perf.txt")
    assert res.ok
    assert res.tolerated_empty == {"remaining_months": 1}
    assert len(res.warnings) == 1
    assert (
        "remaining_months: empty in 1 row(s)" in res.warnings[0]
        and "0.0500% of rows, limit 0.10%" in res.warnings[0]
    )


def test_remaining_months_often_empty_stops_the_load(perf_contract):
    res = dc.check_lines(perf_rows_with_empty_remaining(perf_contract, 500, 1), perf_contract, "perf.txt")
    assert not res.ok
    assert "0.2000% of rows, limit 0.10%" in res.errors[0]
    assert res.tolerated_empty == {"remaining_months": 1}


def test_tolerated_count_is_zero_when_nothing_is_empty(perf_contract):
    assert dc.check_file(PERF, RELEASE).tolerated_empty == {"remaining_months": 0}


def test_other_required_fields_have_no_tolerance(perf_contract):
    rows = [set_field(r, perf_contract, "current_upb", "") for r in lines(PERF)[:1]] + lines(PERF)[1:] * 500
    res = dc.check_lines(rows, perf_contract, "perf.txt")
    assert not res.ok and "current_upb: empty in 1 row(s)" in res.errors[0] and "limit" not in res.errors[0]


def test_tolerance_only_on_required_fields(tmp_path):
    text = (dc.CONTRACT_DIR / "freddie_perf_r48.yml").read_text(encoding="utf-8")
    text = text.replace(
        "{name: legal_costs, type: decimal, level: nullable}",
        "{name: legal_costs, type: decimal, level: nullable, max_empty_share: 0.01}",
    )
    path = tmp_path / "freddie_perf_r48.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="only applies to required fields"):
        dc.load_contract(path)


@pytest.mark.parametrize(
    ("term", "flagged", "ok"),
    [("360", 0, True), ("361", 1, True), ("540", 1, True), ("600", 1, True), ("601", 0, False)],
)
def test_long_terms_are_flagged_and_bounded(orig_contract, term, flagged, ok):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "orig_loan_term", term)
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    assert res.ok is ok
    assert res.flagged == {"orig_loan_term>360": flagged}
    if not ok:
        assert "orig_loan_term: above 600" in res.errors[0]


# --------------------------------------------------------------------------- zips (streamed, never extracted)
def make_zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


def test_zip_with_good_files_passes(tmp_path):
    z = make_zip(
        tmp_path / "sample_2023.zip",
        {"sample_orig_2023.txt": ORIG.read_text(), "sample_perf_2023.txt": PERF.read_text()},
    )
    results = dc.check_zip(z, RELEASE)
    assert [(r.file, r.rows, r.distinct_ids, r.ok) for r in results] == [
        ("sample_2023.zip:sample_orig_2023.txt", 3, 3, True),
        ("sample_2023.zip:sample_perf_2023.txt", 6, 3, True),
    ]
    assert list(tmp_path.iterdir()) == [z]  # nothing was extracted next to the zip


def test_zip_member_in_a_folder_and_folder_entries(tmp_path):
    z = tmp_path / "sample_2023.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("inner/", "")
        zf.writestr("inner/sample_orig_2023.txt", ORIG.read_text())
    results = dc.check_zip(z, RELEASE)
    assert len(results) == 1 and results[0].ok


def test_zip_with_unexpected_file_fails(tmp_path):
    z = make_zip(
        tmp_path / "sample_2023.zip", {"readme.txt": "hello", "sample_orig_2023.txt": ORIG.read_text()}
    )
    results = dc.check_zip(z, RELEASE)
    bad = [r for r in results if not r.ok]
    assert len(bad) == 1 and "readme.txt" in bad[0].errors[0] and "not a Freddie" in bad[0].errors[0]


def test_zip_with_broken_file_fails(tmp_path, orig_contract):
    rows = lines(ORIG)
    rows[0] = rows[0].rsplit("|", 1)[0]
    z = make_zip(tmp_path / "sample_2023.zip", {"sample_orig_2023.txt": "\n".join(rows) + "\n"})
    (res,) = dc.check_zip(z, RELEASE)
    assert not res.ok and "found 30 columns x1" in res.errors[0]


def test_not_available_codes_are_allowed(orig_contract):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "credit_score", "9999")
    rows[0] = set_field(rows[0], orig_contract, "orig_dti", "999")
    assert dc.check_lines(rows, orig_contract, "x").ok


def test_duplicate_loan_id_fails(orig_contract):
    rows = lines(ORIG)
    rows.append(rows[0])
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    assert res.errors == ["orig.txt: freddie_origination.loan_id: duplicate in 1 row(s) (lines [4])"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("zero_balance_code", "06"),  # Fannie Mae's code, not Freddie's
        ("delinquency_status", "R"),
        ("borrower_assistance_status", "X"),
        ("delinquency_due_to_disaster", "N"),
        ("modification_flag", "N"),
    ],
)
def test_unknown_codes_in_default_drivers_fail(perf_contract, name, value):
    rows = lines(PERF)
    rows[0] = set_field(rows[0], perf_contract, name, value)
    res = dc.check_lines(rows, perf_contract, "perf.txt")
    assert not res.ok
    assert f"freddie_performance.{name}: " in res.errors[0]


def test_reo_acquisition_and_capped_status_are_allowed(perf_contract):
    rows = lines(PERF)
    rows[0] = set_field(rows[0], perf_contract, "delinquency_status", "RA")
    rows[1] = set_field(rows[1], perf_contract, "delinquency_status", "99")
    assert dc.check_lines(rows, perf_contract, "x").ok


def test_problems_in_info_fields_only_warn(orig_contract):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "seller_name", "")  # info fields may be empty
    assert dc.check_lines(rows, orig_contract, "x").ok
    # an info field with a rule: make one temporarily to prove info problems are warnings
    rule = dc.FieldRule(name="seller_name", type="int", level="info")
    fields = tuple(rule if f.name == "seller_name" else f for f in orig_contract.fields)
    strict = dc.Contract(**{**orig_contract.__dict__, "fields": fields})
    res = dc.check_lines(lines(ORIG), strict, "x")
    assert res.ok
    assert len(res.warnings) == 1 and "seller_name: not a whole number in 3 row(s)" in res.warnings[0]


def test_examples_are_capped_and_name_lines_not_values(orig_contract):
    rows = [set_field(r, orig_contract, "credit_score", "1") for r in lines(ORIG) * 3]
    res = dc.check_lines(rows, orig_contract, "orig.txt")
    credit = [e for e in res.errors if ".credit_score:" in e]
    assert credit == ["orig.txt: freddie_origination.credit_score: below 300 in 9 row(s) (lines [1, 2, 3])"]
    assert not any("F23Q" in e for e in res.errors)  # loan ids never appear in messages


def test_enforce_stops_on_errors(orig_contract, capsys):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "credit_score", "abc")
    rows[1] = set_field(rows[1], orig_contract, "orig_ltv", "abc")
    bad = dc.check_lines(rows, orig_contract, "orig.txt")
    with pytest.raises(dc.ContractViolation, match=r"data contract broken: .* \(\+1 more, see the log\)"):
        dc.enforce([bad])
    out = capsys.readouterr().out
    assert "FAILED" in out and out.count("ERROR ") == 2


def test_enforce_single_error_message(orig_contract):
    rows = lines(ORIG)
    rows[0] = set_field(rows[0], orig_contract, "credit_score", "abc")
    with pytest.raises(dc.ContractViolation) as err:
        dc.enforce([dc.check_lines(rows, orig_contract, "orig.txt")])
    assert "more" not in str(err.value)
