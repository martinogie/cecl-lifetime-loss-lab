"""Write the website's data files (site/public/data/*.json) for the build and the tests.

    CECL_ENV=local python site/scripts/export_site_data.py        (from the repository root)

The logic lives in ingestion/site_export.py, which the prod publish step (ingestion/site_publish.py) also uses.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository root, for `ingestion`

from ingestion import site_export  # noqa: E402

sys.exit(site_export.main())
