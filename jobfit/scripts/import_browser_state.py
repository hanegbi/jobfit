"""Import the liked/hidden/sent/reached flags exported from the old page.

Those flags lived in one browser's localStorage, which is why they need a
file to travel through: a page served from disk and the app served from
localhost are different origins, and neither can read the other's storage.

Usage: uv run python -m jobfit.scripts.import_browser_state jobfit-my-data.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jobfit.store import db
from jobfit.store import jobs as store_jobs
from jobfit.store import state as store_state

# The page's localStorage key -> the state field it carried.
KEY_TO_FIELD = {
    "jobfit_liked": "liked",
    "jobfit_hidden": "hidden",
    "jobfit_sent": "sent",
    "jobfit_reached": "reached_out",
}


def import_state(conn, exported: dict) -> dict:
    """Returns counts of what was applied and what could not be matched.

    Job ids are base64url of the job's URL and did not change in the move to
    the database, so a flag matches or its job is genuinely gone."""
    applied = {field: 0 for field in KEY_TO_FIELD.values()}
    missing = 0
    for key, field in KEY_TO_FIELD.items():
        for job_id in exported.get(key) or []:
            if store_jobs.get_job(conn, job_id) is None:
                missing += 1
                continue
            store_state.set_state(conn, job_id, **{field: True})
            applied[field] += 1
    return {"applied": applied, "unknown_jobs": missing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="the file the page's 'Export my data' button saved")
    args = parser.parse_args()

    exported = json.loads(args.path.read_text(encoding="utf-8"))
    result = import_state(db.shared(), exported)
    print(f"imported: {result['applied']}")
    if result["unknown_jobs"]:
        print(f"{result['unknown_jobs']} flag(s) referred to jobs that are no longer stored")


if __name__ == "__main__":
    main()
