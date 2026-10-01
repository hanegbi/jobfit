"""Check each open LinkedIn-sourced job for the "No longer accepting
applications" banner and close it in the store when found. LinkedIn jobs come
from matches/referrals that no company scrape re-verifies, so this is how they
age out.

The verdicts are cached in cache/linkedin_closed_check.json, and closing is a
SEPARATE step at the end of a run. A run that is interrupted, throttled out,
or passed --dry-run therefore leaves known-closed jobs open in the store: 650
were known closed while 379 of them were still being served. If that happens
again, re-running applies the cache without re-fetching anything.

Deliberately slow and sequential (not concurrent) - LinkedIn rate-limited even
lightweight HEAD requests hard during the earlier URL-validity check (429 on
~900 of ~2,400 status-only checks), so this paces itself like a human
browsing rather than a bot, and treats "blocked/429/error" as inconclusive
(left alone, not closed) rather than guessing.

Usage: uv run python -m jobfit.scripts.check_linkedin_closed [--limit N] [--delay SECONDS] [--dry-run]
"""

import argparse
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, config  # noqa: E402

logger = logging.getLogger("jobfit.check_linkedin_closed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TIMEOUT = 15
CLOSED_PHRASE = "no longer accepting applications"
OUTPUT_PATH = config.ROOT / "cache" / "linkedin_closed_check.json"
_JOB_ID_RE = re.compile(r"(\d{8,})")
# LinkedIn's public guest endpoint for one posting: ~45 KB, no login, ~1.4 s,
# versus ~4 s for the full page. Same closed banner. Measured 2026-09-29: four
# parallel requests earn 429s within seconds and the throttle then lingers
# over sequential requests too - hence sequential + exponential back-off.
GUEST_POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
BACKOFF_STEPS_S = (30, 60, 120, 300)


def _guest_url(url: str) -> str:
    m = _JOB_ID_RE.search(url)
    return GUEST_POSTING_URL.format(job_id=m.group(1)) if m else url


def _fetch_once(session, url: str) -> str:
    """Return "closed", "open", "blocked", or "error" for a single request."""
    try:
        resp = session.get(_guest_url(url), timeout=TIMEOUT)
    except Exception as error:  # noqa: BLE001
        logger.debug("request failed for %s: %s", url, error)
        return "error"
    if resp.status_code == 429:
        return "blocked"
    if resp.status_code >= 400:
        return "error"
    text = resp.text
    return "closed" if (CLOSED_PHRASE in text.lower() or 'class="closed-job"' in text) else "open"


def check_one(session, url: str) -> str:
    """Return "closed", "open", "blocked", or "error".

    A single request occasionally (rarely, cause unconfirmed - possibly a
    transient LinkedIn edge/cache variant) came back "open" for a job later
    confirmed closed on a fresh request, with zero observed false positives
    the other direction. So: "open" isn't trusted from one request alone -
    immediately re-check once, and treat "closed" from EITHER attempt as
    authoritative.
    """
    first = _fetch_once(session, url)
    if first != "open":
        return first
    time.sleep(0.8)
    second = _fetch_once(session, url)
    return "closed" if second == "closed" else first


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=1.2, help="base seconds between requests")
    parser.add_argument("--dry-run", action="store_true", help="report only; don't close jobs or re-aggregate")
    parser.add_argument("--min-score", type=int, default=0, help="only verify jobs whose best_score is at least this (0 = all)")
    args = parser.parse_args()

    candidates = store_jobs.open_linkedin_ranked(db.shared(), args.min_score)
    linkedin_urls = list(dict.fromkeys(r["url"] for r in candidates))
    logger.info("open LinkedIn job URLs to verify: %d (min score %d)", len(linkedin_urls), args.min_score)

    already = {}
    if OUTPUT_PATH.exists():
        already = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    # Only re-check ones we don't have a conclusive answer for yet.
    # Only "closed" is trusted from a prior run (never seen a false positive);
    # "open" was proven unreliable from a single-request check, so re-verify it.
    todo = [u for u in linkedin_urls if already.get(u) != "closed"]
    logger.info("%d already checked conclusively, %d to check", len(linkedin_urls) - len(todo), len(todo))

    if args.limit:
        todo = todo[: args.limit]

    session = ats_fetchers.make_session(pool_size=4)
    counts = {"closed": 0, "open": 0, "blocked": 0, "error": 0}
    blocked_streak = 0
    for i, url in enumerate(todo, 1):
        status = check_one(session, url)
        if status == "blocked":
            # Throttled: back off (30s, 60s, 120s, 300s...) and retry this one
            # URL once per step rather than burning the rest of the list.
            for pause in BACKOFF_STEPS_S:
                logger.info("429 from LinkedIn - pausing %ds", pause)
                time.sleep(pause)
                status = check_one(session, url)
                if status != "blocked":
                    break
        blocked_streak = blocked_streak + 1 if status == "blocked" else 0
        already[url] = status
        counts[status] += 1
        if blocked_streak >= 3:
            logger.warning("still throttled after back-off on 3 consecutive URLs - stopping; re-run later, results so far are saved")
            break
        if i % 25 == 0 or i == len(todo):
            logger.info("checked %d/%d - closed:%d open:%d blocked:%d error:%d",
                        i, len(todo), counts["closed"], counts["open"], counts["blocked"], counts["error"])
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_PATH.write_text(json.dumps(already, ensure_ascii=False), encoding="utf-8")
        time.sleep(args.delay + random.uniform(0, 0.6))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(already, ensure_ascii=False), encoding="utf-8")

    total_closed = sum(1 for v in already.values() if v == "closed")
    logger.info("done. total closed across all checks so far: %d / %d", total_closed, len(already))
    if args.dry_run:
        return
    from jobfit.scripts import update_jobs

    stats = update_jobs.close_jobs_by_url({u: "linkedin: no longer accepting applications" for u, v in already.items() if v == "closed"})
    logger.info("closed %d job(s) across %d company file(s) (%d were already closed)",
                stats["jobs_closed"], stats["companies_touched"], stats["already_closed"])
    if stats["jobs_closed"]:
        update_jobs.recompute_stage()


if __name__ == "__main__":
    main()
