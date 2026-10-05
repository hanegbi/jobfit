"""Delete stored rows that were never job postings: a company's own
glossary, partners, pricing and product pages, and demo CTAs, scraped off
a careers URL that listed nothing.

The rule is filters.non_job_reason - the same vocabulary the scrape-time
link filters use, so what the scraper now rejects and what this removes
cannot drift apart. Everything it finds is written to a backup JSON before
anything is deleted, and any row carrying the user's own liked/hidden/sent
flags is left alone and reported rather than destroyed.

Usage: uv run python -m jobfit.scripts.purge_non_jobs [--dry-run] [--limit N]
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import config  # noqa: E402
from jobfit.scrape.filters import non_job_reason  # noqa: E402
from jobfit.store import db, jobs as store_jobs  # noqa: E402

BACKUP_DIR = config.ROOT / "cache" / "purged"


def find_non_jobs(conn) -> list[dict]:
    """Every stored row the non-job rule rejects, with the reason."""
    found = []
    for row in conn.execute("SELECT id, company_id, title, url, status FROM jobs WHERE url IS NOT NULL"):
        reason = non_job_reason(row["title"], row["url"])
        if reason:
            found.append({**dict(row), "reason": reason})
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report and write the backup; delete nothing")
    parser.add_argument("--limit", type=int, default=None, help="only act on the first N (for a cautious first run)")
    args = parser.parse_args()

    conn = db.shared()
    found = find_non_jobs(conn)
    if args.limit:
        found = found[: args.limit]

    # A row the user has an opinion about is theirs, not ours to delete -
    # even if the heuristic thinks it is a product page.
    flagged = {r["job_id"] for r in conn.execute("SELECT job_id FROM job_state")}
    protected = [r for r in found if r["id"] in flagged]
    doomed = [r for r in found if r["id"] not in flagged]

    total = conn.execute("SELECT count(*) c FROM jobs").fetchone()["c"]
    print(f"stored jobs: {total}")
    print(f"judged not a posting: {len(found)}  (open {sum(1 for r in found if r['status'] != 'closed')})")
    for reason, count in Counter(r["reason"].split(" '")[0] for r in found).most_common():
        print(f"  {reason}: {count}")
    if protected:
        print(f"\nLEFT ALONE - you have flags on these {len(protected)}:")
        for r in protected[:20]:
            print(f"  {r['title'][:60]}".encode("ascii", "replace").decode("ascii"))

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_DIR / f"purged-{stamp}.json"
    backup.write_text(json.dumps(doomed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nbackup of {len(doomed)} row(s) written to {backup}")

    if args.dry_run:
        print("dry run - nothing deleted")
        return

    stats = store_jobs.delete_jobs(conn, [r["id"] for r in doomed])
    print(f"deleted {stats['jobs']} job row(s), {stats['scores']} score row(s), {stats['state']} state row(s)")
    print(f"stored jobs now: {conn.execute('SELECT count(*) c FROM jobs').fetchone()['c']}")


if __name__ == "__main__":
    main()
