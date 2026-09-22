"""Force-refresh the cached techmap job-listing CSVs straight from GitHub.

techmap (https://github.com/mluggy/techmap) updates its category CSVs on its
own schedule; this repo caches them locally (jobfit/cache/techmap/*.csv) with
no TTL, so a plain run of update_jobs.py never re-downloads them - it just
reads whatever's already on disk. This script does the one thing needed to
pick up techmap's latest data: re-download every category CSV, overwriting
the cache.

Usage: uv run python -m jobfit.scripts.update_techmap
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, config, techmap_source  # noqa: E402

logger = logging.getLogger("jobfit.update_techmap")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    session = ats_fetchers.make_session()
    rows = techmap_source.load_all_rows(session, force=True)
    companies = {row["company"] for row in rows}
    logger.info(
        "refreshed %d categories: %d rows, %d unique companies",
        len(config.TECHMAP_CATEGORIES), len(rows), len(companies),
    )


if __name__ == "__main__":
    main()
