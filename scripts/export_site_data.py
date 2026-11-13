"""Export aggregated results for the website (Phase 7 placeholder).

Only prod publishes: the first thing this does is check CECL_ENV=prod, so a dev or local run can
never overwrite the public site data. It will only ever export aggregates (never loan rows, per
Freddie Mac's terms), each with its date, scenario and limits.

    CECL_ENV=prod python scripts/export_site_data.py
"""

from __future__ import annotations

import sys

from cecl import environment


def main() -> int:
    environment.assert_can_publish("site data")
    print("Phase 7 placeholder: nothing to export yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
