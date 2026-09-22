"""Check each LinkedIn-sourced job for the "No longer accepting applications"
banner and flag it for removal if closed.

Deliberately slow and sequential (not concurrent) - LinkedIn rate-limited even
lightweight HEAD requests hard during the earlier URL-validity check (429 on
~900 of ~2,400 status-only checks), so this paces itself like a human
browsing rather than a bot, and treats "blocked/429/error" as inconclusive
(left alone, not removed) rather than guessing.

Usage: uv run python -m jobfit.scripts.check_linkedin_closed [--limit N] [--delay SECONDS]
"""

import argparse
import json
import logging
import random
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


def _fetch_once(session, url: str) -> str:
    """Return "closed", "open", "blocked", or "error" for a single request."""
    try:
        resp = session.get(url, timeout=TIMEOUT)
    except Exception as error:  # noqa: BLE001
        logger.debug("request failed for %s: %s", url, error)
        return "error"
    if resp.status_code == 429:
        return "blocked"
    if resp.status_code >= 400:
        return "error"
    return "closed" if CLOSED_PHRASE in resp.text.lower() else "open"


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
    args = parser.parse_args()

    data = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    linkedin_urls = sorted({r["url"] for r in data if r.get("url") and "linkedin.com" in r["url"].lower()})
    logger.info("total unique LinkedIn URLs: %d", len(linkedin_urls))

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
    for i, url in enumerate(todo, 1):
        status = check_one(session, url)
        already[url] = status
        counts[status] += 1
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


if __name__ == "__main__":
    main()
