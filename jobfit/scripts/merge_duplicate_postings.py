"""Reopen closed jobs that are, by normalized URL, the exact same posting
as an OPEN job at the same company - the Tikalk bug: a company switches
ATS host (or appends a volatile query parameter normalize_job_url now
strips) mid-scrape, so the same real posting gets two different stored
URLs and two different ids, and the one the next scrape no longer
revisits closes while its twin stays open.

Pure DB logic, no network: a duplicate is proven by URL identity, not a
liveness guess, so unlike check_urls.py there is nothing here that could
mistake a still-live blog post or product page for a job.

Usage: uv run python -m jobfit.scripts.merge_duplicate_postings [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit.store import db, jobs as store_jobs  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report only; don't reopen jobs or rescore")
    args = parser.parse_args()

    conn = db.shared()
    duplicates = store_jobs.duplicate_closed_jobs(conn)
    print(f"closed jobs that duplicate an open sibling's URL: {len(duplicates)}")
    for d in duplicates[:30]:
        line = f"  {d['company_id']} | {d['title'][:50]} | closed {d['id']} <-> open {d['open_sibling_id']}"
        print(line.encode("ascii", "replace").decode("ascii"))
    if len(duplicates) > 30:
        print(f"  ... and {len(duplicates) - 30} more")

    if args.dry_run or not duplicates:
        return

    from jobfit.scripts import update_jobs

    stats = update_jobs.reopen_jobs_by_url([d["url"] for d in duplicates])
    print(f"\nreopened {stats['jobs_reopened']} job(s) ({stats['already_open']} were already open)")
    if stats["jobs_reopened"]:
        update_jobs.recompute_stage()


if __name__ == "__main__":
    main()
