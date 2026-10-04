"""Audit every open job's URL for validity - HEAD request (GET fallback for
servers that reject HEAD), concurrent, short timeout. A URL that comes back
404/410 is a posting that is gone: the job is closed in the store (then
rescored, since a closed job drops out of recompute_stage).

Usage: uv run python -m jobfit.scripts.check_urls [--limit N] [--workers N] [--dry-run]
"""

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, config  # noqa: E402
from jobfit.store import db, jobs as store_jobs  # noqa: E402

TIMEOUT = 10
OUTPUT_PATH = config.ROOT / "cache" / "url_check_report.json"
PROGRESS_PATH = config.ROOT / "cache" / "url_check_progress.json"


def check_one(session: requests.Session, url: str) -> tuple[str, str]:
    """Return (status, detail) - status one of ok/broken/error/blocked."""
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
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true", help="report only; don't close jobs or rescore")
    parser.add_argument("--max-age-hours", type=float, default=24, help="reuse a verdict from the progress file younger than this")
    args = parser.parse_args()

    conn = db.shared()
    jobs_with_url = store_jobs.open_with_url(conn)
    print(f"total jobs: {store_jobs.counts(conn)['total']}, open with a URL: {len(jobs_with_url)}", flush=True)

    # Resumable: a run over ~25k URLs takes hours, so verdicts are saved as we
    # go and a re-run skips anything checked recently.
    progress: dict[str, dict] = {}
    if PROGRESS_PATH.exists():
        try:
            progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            progress = {}
    cutoff = time.time() - args.max_age_hours * 3600
    results: dict[str, tuple[str, str]] = {u: (v["status"], v["detail"]) for u, v in progress.items() if v.get("checked_at", 0) >= cutoff}

    unique_urls = [u for u in {r["url"] for r in jobs_with_url} if u not in results]
    if args.limit:
        unique_urls = unique_urls[: args.limit]
    print(f"unique URLs to check: {len(unique_urls)} ({len(results)} reused from the last {args.max_age_hours:g}h)", flush=True)

    def save_progress():
        PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS_PATH.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")

    session = ats_fetchers.make_session(pool_size=max(32, args.workers))
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_one, session, url): url for url in unique_urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as error:  # noqa: BLE001
                results[url] = ("error", type(error).__name__)
            progress[url] = {"status": results[url][0], "detail": results[url][1], "checked_at": time.time()}
            done += 1
            if done % 200 == 0:
                print(f"checked {done}/{len(unique_urls)}", flush=True)
            if done % 500 == 0:
                save_progress()
    save_progress()

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
                    "company_id": job["company_id"], "title": job["title"], "url": url,
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
        line = f"  [{j['status']}:{j['detail']}] {j['company_id']} | {j['title'][:50]} | {j['url'][:80]}"
        print(line.encode("ascii", "replace").decode("ascii"))

    if args.dry_run:
        return
    from jobfit.scripts import update_jobs

    # Only a definite "gone" closes a job: 404/410. Errors and bot-blocks don't.
    gone = {url: f"url: http {d}" for url, (s, d) in results.items() if s == "broken" and d in ("404", "410")}
    stats = update_jobs.close_jobs_by_url(gone)
    print(f"\nclosed {stats['jobs_closed']} job(s) ({stats['already_closed']} already closed) for {len(gone)} gone URL(s)")
    if stats["jobs_closed"]:
        update_jobs.recompute_stage()


if __name__ == "__main__":
    main()
