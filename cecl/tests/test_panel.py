"""One synthetic loan per ADR-002 rule. Months are YYYYMM; statuses are Freddie's codes."""

from __future__ import annotations

import pytest

from cecl.panel import STATUSES, LoanMonth, build_loan_panel


def months(*statuses: str | None, start: int = 202001, **flags_by_index: dict) -> list[LoanMonth]:
    """Consecutive months from `start` with the given delinquency statuses."""
    out = []
    year, month = divmod(start, 100)
    for i, status in enumerate(statuses):
        extra = flags_by_index.get(f"m{i}", {})
        out.append(LoanMonth(period=year * 100 + month, delinquency_status=status, **extra))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def outcome(rows):
    return [(r.status, r.exit_reason) for r in rows]


def last(rows):
    return (rows[-1].status, rows[-1].exit_reason, len(rows))


# --------------------------------------------------------------------------- basics
def test_always_current_is_censored_at_data_end():
    rows = build_loan_panel(months("00", "00", "00"))
    assert outcome(rows) == [("current", None), ("current", None), ("censored", "censored_data_end")]


def test_empty_loan_has_no_rows():
    assert build_loan_panel([]) == []


def test_months_must_be_in_order():
    m = months("00", "00")
    with pytest.raises(ValueError, match="increasing period order"):
        build_loan_panel([m[1], m[0]])
    with pytest.raises(ValueError):
        build_loan_panel([m[0], m[0]])


def test_delinquent_months_are_marked():
    rows = build_loan_panel(months("00", "01", "02", "00", "00"))
    assert [r.status for r in rows[:4]] == ["current", "delinquent", "delinquent", "current"]


def test_every_status_is_a_known_one():
    rows = build_loan_panel(months("00", "01", "00"))
    assert {r.status for r in rows} <= set(STATUSES)


# --------------------------------------------------------------------------- A. triggers
def test_90_days_late_is_a_default_and_rows_stop():
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "00"))
    assert last(rows) == ("defaulted", "default_90_days", 4)  # B: first default only


def test_reo_acquisition_is_a_default():
    assert last(build_loan_panel(months("00", "02", "RA"))) == ("defaulted", "default_reo_acquisition", 3)


@pytest.mark.parametrize(
    ("code", "reason"),
    [("02", "default_third_party_sale"), ("03", "default_short_sale"), ("09", "default_reo_disposition")],
)
def test_loss_codes_are_defaults(code, reason):
    rows = build_loan_panel(months("00", "01", "02", m2={"zero_balance_code": code}))
    assert last(rows) == ("defaulted", reason, 3)


def test_code_15_is_a_default_only_when_90_days_late():
    late = months("00", "01", "02", "03")
    late[-1] = LoanMonth(late[-1].period, "03", zero_balance_code="15")
    # 90+ days in the same month as the sale: counted as the note-sale default
    assert last(build_loan_panel(late)) == ("defaulted", "default_note_sale_90_days", 4)
    current = months("00", "00", m1={"zero_balance_code": "15"})
    assert last(build_loan_panel(current)) == ("censored", "censored_note_sale", 2)


@pytest.mark.parametrize(
    ("code", "reason"), [("16", "censored_reperforming_sale"), ("96", "censored_defect")]
)
def test_codes_16_and_96_are_censored(code, reason):
    assert last(build_loan_panel(months("00", "00", m1={"zero_balance_code": code}))) == (
        "censored",
        reason,
        2,
    )


def test_code_01_is_a_prepayment():
    assert last(build_loan_panel(months("00", "00", m1={"zero_balance_code": "01"}))) == (
        "prepaid",
        "prepaid",
        2,
    )


# --------------------------------------------------------------------------- C. modifications
def test_modified_while_60_days_late_is_a_default():
    rows = build_loan_panel(months("00", "01", "02", m2={"modification_flag": "Y"}))
    assert last(rows) == ("defaulted", "default_modification_60_days", 3)


def test_modified_while_30_days_late_is_not_a_default():
    rows = build_loan_panel(months("00", "01", "00", "00", m1={"modification_flag": "Y"}))
    assert last(rows) == ("censored", "censored_data_end", 4)
    assert all(r.status != "defaulted" for r in rows)


# --------------------------------------------------------------------------- D. stop the clock
def paused_months(n: int, start_late: int = 1, flag: dict | None = None) -> dict:
    flag = flag or {"borrower_assistance_status": "F"}
    return {f"m{i}": flag for i in range(1, 1 + n)}


def test_forbearance_months_do_not_count_toward_90_days():
    # six months in forbearance: Freddie reports 1..6 months late, but the stopped clock stays at 0
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "05", "06", **paused_months(6)))
    assert all(r.status != "defaulted" for r in rows)
    assert [r.effective_months_late for r in rows] == [0] * 7
    assert [r.reported_months_late for r in rows] == [0, 1, 2, 3, 4, 5, 6]


def test_disaster_flag_stops_the_clock_too():
    flag = {"delinquency_due_to_disaster": "Y"}
    rows = build_loan_panel(months("00", "01", "02", "03", "04", **paused_months(4, flag=flag)))
    assert all(r.status != "defaulted" for r in rows)


def test_deferral_when_forbearance_ends_brings_the_loan_current():
    flags = paused_months(6)
    flags["m7"] = {"payment_deferral_flag": "C"}
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "05", "06", "00", "00", **flags))
    assert all(r.status != "defaulted" for r in rows)
    assert rows[7].status == "current"


def test_covid_loan_modified_when_forbearance_ends_is_not_a_default():
    """Martins' case (a): forbearance ends, and the next month the loan is modified while Freddie
    still reports it 7 months late. On the stopped clock it is only 1 month late, under the 2-month
    (60-day) threshold, so the modification is not a default; lateness then resets to current."""
    flags = paused_months(6)
    flags["m7"] = {"modification_flag": "Y"}
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "05", "06", "07", "08", **flags))
    assert rows[7].effective_months_late == 0 and rows[7].status == "current"
    assert all(r.status != "defaulted" for r in rows)
    # after the reset, a new missed payment counts from zero again
    assert rows[8].effective_months_late == 1


def test_forbearance_ends_and_modification_in_the_same_month_is_not_a_default():
    """Martins' case (b): the last forbearance month is also the modification month."""
    flags = paused_months(5)
    flags["m6"] = {"borrower_assistance_status": "F", "modification_flag": "Y"}
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "05", "06", "00", **flags))
    assert rows[6].status == "current" and rows[6].effective_months_late == 0
    assert all(r.status != "defaulted" for r in rows)


def test_without_deferral_the_count_picks_up_where_it_stopped():
    # 6 paused months, then no help: 7, 8, 9 months reported = 1, 2, 3 on the stopped clock
    rows = build_loan_panel(
        months("00", "01", "02", "03", "04", "05", "06", "07", "08", "09", **paused_months(6))
    )
    assert [r.effective_months_late for r in rows] == [0, 0, 0, 0, 0, 0, 0, 1, 2, 3]
    assert last(rows) == ("defaulted", "default_90_days", 10)


def test_lateness_before_the_pause_is_kept():
    # 2 months late before forbearance starts; paused months don't add; the next missed month defaults
    flags = {"m3": {"borrower_assistance_status": "F"}, "m4": {"borrower_assistance_status": "F"}}
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "05", **flags))
    assert [r.effective_months_late for r in rows] == [0, 1, 2, 2, 2, 3]
    assert last(rows) == ("defaulted", "default_90_days", 6)


def test_modification_at_60_days_after_a_pause_uses_the_stopped_clock():
    # pause then two real missed payments (stopped clock 2), then modified: a default under C
    flags = paused_months(3)
    flags["m6"] = {"modification_flag": "Y"}
    rows = build_loan_panel(months("00", "01", "02", "03", "04", "05", "05", **flags))
    assert last(rows) == ("defaulted", "default_modification_60_days", 7)


def test_being_current_again_clears_the_paused_count():
    flags = paused_months(2)
    rows = build_loan_panel(months("00", "01", "02", "00", "01", "02", "03", **flags))
    assert [r.effective_months_late for r in rows] == [0, 0, 0, 0, 1, 2, 3]
    assert last(rows) == ("defaulted", "default_90_days", 7)


def test_deferral_without_a_pause_does_not_reset():
    rows = build_loan_panel(months("00", "01", "02", "02", m3={"payment_deferral_flag": "C"}))
    assert rows[3].effective_months_late == 2


# --------------------------------------------------------------------------- E. missing status
def test_missing_status_is_carried_forward_up_to_3_months():
    rows = build_loan_panel(months("00", "01", "XX", "XX", "XX", "00"))
    assert [r.reported_months_late for r in rows[2:5]] == [1, 1, 1]
    assert all(r.status_carried_forward for r in rows[2:5])
    assert last(rows) == ("censored", "censored_data_end", 6)


def test_a_4th_missing_month_censors_the_loan():
    rows = build_loan_panel(months("00", "01", "XX", "XX", "XX", "XX", "00"))
    assert last(rows) == ("censored", "censored_missing_status", 6)


def test_missing_status_before_any_known_status_censors():
    assert last(build_loan_panel(months("XX", "00"))) == ("censored", "censored_missing_status", 1)


def test_missing_status_counts_restart_after_a_real_one():
    rows = build_loan_panel(months("00", "XX", "XX", "XX", "00", "XX", "XX", "XX", "00"))
    assert last(rows) == ("censored", "censored_data_end", 9)


def test_empty_status_is_treated_as_missing():
    rows = build_loan_panel(months("00", None, "00"))
    assert rows[1].status_carried_forward


def test_carried_lateness_can_still_reach_default_with_new_data():
    rows = build_loan_panel(months("00", "02", "XX", "03"))
    assert last(rows) == ("defaulted", "default_90_days", 4)


# --------------------------------------------------------------------------- E clarification: XX at loan age 0
def test_xx_at_loan_age_0_is_not_yet_due_and_treated_as_current():
    """Since October 2025 Freddie reports the month before the first payment as XX (it used to be 00)."""
    rows = build_loan_panel(months("XX", "00", "00", m0={"loan_age": 0}))
    assert rows[0].status == "current" and rows[0].status_not_yet_due
    assert not rows[0].status_carried_forward
    assert last(rows) == ("censored", "censored_data_end", 3)


def test_new_loan_with_only_its_first_month_is_censored_at_data_end_not_for_missing_status():
    rows = build_loan_panel(months("XX", m0={"loan_age": 0}))
    assert last(rows) == ("censored", "censored_data_end", 1)
    assert rows[0].status_not_yet_due


def test_xx_after_loan_age_0_still_follows_rule_e():
    flags = {
        "m0": {"loan_age": 0},
        "m1": {"loan_age": 1},
        "m2": {"loan_age": 2},
        "m3": {"loan_age": 3},
        "m4": {"loan_age": 4},
    }
    rows = build_loan_panel(months("XX", "XX", "XX", "XX", "XX", **flags))
    # month 0: not yet due; months 1-3 carried forward; month 4 is the 4th missing month -> censored
    assert [r.status_not_yet_due for r in rows] == [True, False, False, False, False]
    assert [r.status_carried_forward for r in rows[1:4]] == [True, True, True]
    assert last(rows) == ("censored", "censored_missing_status", 5)


def test_xx_as_first_record_at_a_later_age_is_still_censored():
    rows = build_loan_panel(months("XX", "00", m0={"loan_age": 1}))
    assert last(rows) == ("censored", "censored_missing_status", 1)
    assert not rows[0].status_not_yet_due


def test_loan_age_0_with_a_real_status_is_unaffected():
    rows = build_loan_panel(months("00", "00", m0={"loan_age": 0}))
    assert not rows[0].status_not_yet_due
