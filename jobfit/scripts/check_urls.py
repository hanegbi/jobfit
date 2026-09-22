"""Audit every job's URL for validity - HEAD request (GET fallback for servers
that reject HEAD), concurrent, short timeout. Reports broken links so they can
be reviewed/dropped; does not modify the dataset itself.

Usage: uv run python -m jobfit2.scripts.check_urls [--limit N] [--workers N]
"""

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit2 import ats_fetchers, config  # noqa: E402

TIMEOUT = 10
OUTPUT_PATH = config.ROOT / "cache" / "url_check_report.json"


def check_one(session: requests.Session, url: str) -> tuple[str, str]:
    """Return (status, detail) - status one of ok/redirect/broken/error/blocked."""
    try:
        resp = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code == 405 or resp.status_code >= 400:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            resp.close()
    except requests.RequestException as error:
        return "error", type(error).__name__

    code = resp.status_code
    if code in (403, 999):
        return "blocked", str(code)
    if 200 <= code < 300:
        return "ok", str(code)
    if 300 <= code < 400:
        return "ok", f"redirect {code}"
    if code == 404 or code == 410:
        return "broken", str(code)
    return "broken", str(code)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()

    data = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    jobs_with_url = [r for r in data if r.get("url")]
    print(f"total jobs: {len(data)}, with a URL: {len(jobs_with_url)}")

    unique_urls = list({r["url"] for r in jobs_with_url})
    if args.limit:
        unique_urls = unique_urls[: args.limit]
    print(f"unique URLs to check: {len(unique_urls)}")

    session = ats_fetchers.make_session(pool_size=max(32, args.workers))
    results: dict[str, tuple[str, str]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_one, session, url): url for url in unique_urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as error:  # noqa: BLE001
                results[url] = ("error", type(error).__name__)
            done += 1
            if done % 200 == 0:
                print(f"checked {done}/{len(unique_urls)}")

    status_counts = Counter(status for status, _ in results.values())
    print("\n--- by URL ---")
    for status, count in status_counts.most_common():
        print(f"  {status}: {count}")

    url_to_jobs: dict[str, list[dict]] = {}
    for r in jobs_with_url:
        url_to_jobs.setdefault(r["url"], []).append(r)

    broken_jobs = []
    for url, (status, detail) in results.items():
        if status in ("broken", "error"):
            for job in url_to_jobs.get(url, []):
                broken_jobs.append({
                    "company": job["company"], "title": job["title"], "url": url,
                    "status": status, "detail": detail, "id": job["id"],
                })

    checked_count = len(results)
    print(f"\nbroken/error job links (among URLs checked): {len(broken_jobs)} / {checked_count} URLs checked "
          f"({100 * len(broken_jobs) / max(1, len(jobs_with_url)):.1f}% of all job URLs)")

    # Write the report BEFORE any further printing - a console encoding crash on a
    # Hebrew/unicode title must never cost the results after minutes of checking.
    report = {
        "url_status": {url: {"status": s, "detail": d} for url, (s, d) in results.items()},
        "broken_jobs": broken_jobs,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"\nfull report written to {OUTPUT_PATH}")

    print("\nsample broken links:")
    for j in broken_jobs[:30]:
        line = f"  [{j['status']}:{j['detail']}] {j['company']} | {j['title'][:50]} | {j['url'][:80]}"
        print(line.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    main()
