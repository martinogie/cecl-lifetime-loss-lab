"""The monthly loan panel: one row per loan per month, built exactly to ADR-002 (definition of default).

`build_loan_panel` takes one loan's monthly records, in month order, and returns one row per month
until the loan leaves the data. Each row has a status: current, delinquent, prepaid, defaulted or
censored (left the data without defaulting or prepaying). Only the last row of a loan can be
prepaid, defaulted or censored.

The rules (ADR-002, decided 14 November 2026):

A. Default when effective lateness reaches 3 months (90+ days), or the status is RA (the lender took
   the house), or the loan ends with a loss code: 02 third-party sale, 03 short sale, 09 REO
   disposition. Code 15 (note sale) is a default only if the loan is 90+ days late; codes 16
   (reperforming sale) and 96 (seller defect) are censored. Code 01 is a prepayment.
   Clarification (L-020, decided 15 November 2026): a loan 90+ days late (stopped clock) in the same
   month it pays off (01) is a default that lost nothing, not a prepayment. A loan 90+ days late in
   the month it gets a defect code (96) stays censored.
B. First default only: the loan's rows stop at its first default.
C. A modification while 60+ days late (2+ months) is a default, using the STOPPED-CLOCK lateness
   from D. So a COVID borrower modified as forbearance ends is not a default.
D. Stop the clock: in months flagged as forbearance (assistance status F) or disaster-related
   (delinquency due to disaster Y), extra lateness doesn't count. When the pause ends, a payment
   deferral or modification resets lateness to current; otherwise the count picks up where it
   stopped. (The flags exist only from January 2014: L-016.)
E. A missing status (XX) carries the previous status forward for up to 3 months; a 4th missing month
   censors the loan.
   Clarification (approved 14 November 2026): XX at loan age 0 is not a missing status. Since October
   2025 Freddie reports the month before a loan's first payment is due as XX (before then, as 00), so
   at loan age 0 only, XX means "not yet due" and is treated as current. XX at any later age follows E.

How the stopped clock is counted. Freddie's reported lateness counts months since the last paid
installment, so it keeps rising during an agreed pause. `paused` holds the lateness that built up in
flagged months; effective lateness = reported lateness - paused. `paused` goes back to zero whenever
the borrower is current again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

DEFAULT_MONTHS = 3  # 90+ days late (A)
MODIFICATION_DEFAULT_MONTHS = 2  # modified while 60+ days late (C)
MAX_CARRIED_MONTHS = 3  # XX carried forward at most this long (E)

PREPAID = "01"
LOSS_CODES = {"02": "default_third_party_sale", "03": "default_short_sale", "09": "default_reo_disposition"}
NOTE_SALE = "15"
CENSOR_CODES = {"16": "censored_reperforming_sale", "96": "censored_defect"}

STATUSES = ("current", "delinquent", "prepaid", "defaulted", "censored")


@dataclass(frozen=True)
class LoanMonth:
    """One monthly record of one loan (silver fields the rules need)."""

    period: int  # YYYYMM, e.g. 202004
    delinquency_status: str | None  # "00".."99", "RA", "XX" or None
    zero_balance_code: str | None = None
    modification_flag: str | None = None  # "Y" this month, "P" earlier
    payment_deferral_flag: str | None = None  # "C" this month, "P" earlier
    borrower_assistance_status: str | None = None  # "F" forbearance, "R", "T"
    delinquency_due_to_disaster: str | None = None  # "Y"
    loan_age: int | None = None  # 0 = the month before the first payment is due

    @property
    def flagged_pause(self) -> bool:
        return self.borrower_assistance_status == "F" or self.delinquency_due_to_disaster == "Y"


@dataclass(frozen=True)
class PanelRow:
    period: int
    status: str  # one of STATUSES
    exit_reason: str | None  # why the loan left the data on this row; None while it is still alive
    reported_months_late: int | None  # Freddie's figure (None for RA or an uncarried XX)
    effective_months_late: int  # the stopped-clock figure used by the rules
    flagged_pause: bool
    status_carried_forward: bool  # this month's status was XX and the previous one was used (E)
    status_not_yet_due: bool = False  # XX at loan age 0: before the first payment, treated as current


def _months(status: str) -> int:
    return int(status)


def build_loan_panel(months: Sequence[LoanMonth]) -> list[PanelRow]:
    """Apply ADR-002 to one loan. Returns one row per input month, up to and including the exit."""
    periods = [m.period for m in months]
    if any(b <= a for a, b in zip(periods, periods[1:], strict=False)):
        raise ValueError("months must be in strictly increasing period order, without duplicates")

    rows: list[PanelRow] = []
    paused = 0  # lateness built up in flagged months, which doesn't count (D)
    paused_this_spell = False  # was there a flagged pause since the borrower was last current?
    previous_reported = 0
    last_known: int | None = None  # last real reported lateness, for carrying XX forward (E)
    carried = 0
    effective = 0

    def exit_row(m: LoanMonth, status: str, reason: str, reported: int | None, carried_fwd: bool) -> None:
        rows.append(
            PanelRow(m.period, status, reason, reported, effective, m.flagged_pause, carried_fwd, not_yet_due)
        )

    for m in months:
        raw = m.delinquency_status
        carried_fwd = False
        # E clarification: XX at loan age 0 is Freddie's "first payment not yet due" (from Oct 2025)
        not_yet_due = raw == "XX" and m.loan_age == 0

        if not_yet_due:
            carried, reported, last_known = 0, 0, 0
        # E. missing status: carry the previous one forward for up to 3 months
        elif raw is None or raw == "XX":
            carried += 1
            if last_known is None or carried > MAX_CARRIED_MONTHS:
                exit_row(m, "censored", "censored_missing_status", None, False)
                return rows
            reported = last_known
            carried_fwd = True
        elif raw == "RA":
            # A. the lender took the house
            exit_row(m, "defaulted", "default_reo_acquisition", None, False)
            return rows
        else:
            carried = 0
            reported = _months(raw)
            last_known = reported

        # D. stop the clock during flagged months
        if reported == 0:
            paused, paused_this_spell = 0, False
        elif m.flagged_pause:
            paused += max(0, reported - previous_reported)
            paused_this_spell = True
        effective = max(0, reported - paused)

        # A. loan-ending codes
        code = m.zero_balance_code
        if code in LOSS_CODES:
            exit_row(m, "defaulted", LOSS_CODES[code], reported, carried_fwd)
            return rows
        if code == NOTE_SALE:
            if effective >= DEFAULT_MONTHS:
                exit_row(m, "defaulted", "default_note_sale_90_days", reported, carried_fwd)
            else:
                exit_row(m, "censored", "censored_note_sale", reported, carried_fwd)
            return rows
        if code in CENSOR_CODES:
            exit_row(m, "censored", CENSOR_CODES[code], reported, carried_fwd)
            return rows
        if code == PREPAID:
            # L-020 (decided 15 Nov 2026): 90+ days late (stopped clock) in the payoff month is a
            # default under rule A, read strictly; LGD records it as a default that lost nothing.
            # (A same-month defect code 96 stays censored: handled above.)
            if effective >= DEFAULT_MONTHS:
                exit_row(m, "defaulted", "default_90_days_at_payoff", reported, carried_fwd)
            else:
                exit_row(m, "prepaid", "prepaid", reported, carried_fwd)
            return rows

        # A. 90+ days late on the stopped clock
        if effective >= DEFAULT_MONTHS:
            exit_row(m, "defaulted", "default_90_days", reported, carried_fwd)
            return rows
        # C. modified while 60+ days late, also on the stopped clock
        if m.modification_flag == "Y" and effective >= MODIFICATION_DEFAULT_MONTHS:
            exit_row(m, "defaulted", "default_modification_60_days", reported, carried_fwd)
            return rows

        # D. after a pause, a deferral or modification brings the loan back to current
        if paused_this_spell and (m.payment_deferral_flag == "C" or m.modification_flag == "Y"):
            paused, paused_this_spell = reported, False  # everything reported so far is forgiven
            effective = 0

        rows.append(
            PanelRow(
                m.period,
                "current" if effective == 0 else "delinquent",
                None,
                reported,
                effective,
                m.flagged_pause,
                carried_fwd,
                not_yet_due,
            )
        )
        previous_reported = reported

    # The data ended with the loan still alive: censored at its last month.
    if rows:
        rows[-1] = replace(rows[-1], status="censored", exit_reason="censored_data_end")
    return rows
