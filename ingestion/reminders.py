"""The quarterly reminder (Phase 8; DAG `cecl_quarterly_reminder`): Freddie's new release is due, start the close.

Freddie Mac publishes the Single-Family Loan-Level Dataset quarterly (January, April, July and October; no fixed
day), each release adding performance up to the quarter-end about four months earlier. The reminder goes to the
`[CECL]` Slack and Discord channels on the 15th of those months. It only reminds: Freddie's terms do not cover
scripted downloads, so Martins downloads the files in the browser (ADR-001).
"""

from __future__ import annotations

from datetime import date

from cecl import close

RELEASE_MONTHS = (1, 4, 7, 10)


def quarter_to_close(today: date) -> str:
    """The quarter-end the release due this month lets us close: two quarter-ends back from this month
    (January 2027 -> 202609, April 2027 -> 202612)."""
    return close.prior_quarter(close.prior_quarter(close.quarter_end_ym(today)))


def text(today: date) -> str:
    R = quarter_to_close(today)
    return (f"Freddie Mac's quarterly loan data is due this month (performance to {R[:4]}-{R[4:]}). When it is out "
            "(check the Clarity portal every few days): 1) download the sample zips in the browser and read the release "
            "notes for layout changes; 2) upload them to the prod volume freddie/sample/; 3) record the overlay review for "
            f"reserve_ym {R} and merge it; 4) trigger cecl_quarterly_close with reserve_ym={R} and the release number "
            "(not a Monday before 13:00; at least 4 hours before 02:30). The website then updates by itself.")  # fmt: skip
