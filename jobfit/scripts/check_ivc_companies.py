"""Audit the companies harvested from IVC-Online for dead/closed domains -
same HEAD/GET-with-fallback check as check_urls.py, applied to company
homepages and discovered career URLs instead of job listing URLs. Reports
only; does not modify companies_career_pages.json or company_review.json
(what to do with a dead company - skip vs leave pending - is a review-panel
decision, not this script's).

Checks two sets:
  - every company in cache/ivc_companies_raw.json (its IVC-given homepage)
  - every one of those that also has a real career_url in
    companies_career_pages.json (the discovered URL, which may 404 even if
    the bare homepage is still up)

Usage: uv run python -m jobfit.scripts.check_ivc_companies [--limit N] [--workers N]
"""

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, company_review, config  # noqa: E402
from jobfit.atomic_io import write_json_atomic  # noqa: E402
from jobfit.scripts.check_urls import check_one  # noqa: E402

OUTPUT_PATH = config.ROOT / "cache" / "ivc_company_check_report.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()

    raw_path = config.ROOT / "cache" / "ivc_companies_raw.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    career_pages = company_review.load_career_pages()

    checks: dict[str, str] = {}  # url -> company name (for reporting)
    for name, homepage in raw.items():
        checks[homepage] = name
        real_url = career_pages.get(name)
        if real_url and real_url != homepage:
            checks[real_url] = name

    urls = list(checks)
    if args.limit:
        urls = urls[: args.limit]
    print(f"companies in raw harvest: {len(raw)}, URLs to check: {len(urls)}")

    session = ats_fetchers.make_session(pool_size=max(32, args.workers))
    results: dict[str, tuple[str, str]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_one, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as error:  # noqa: BLE001
                results[url] = ("error", type(error).__name__)
            done += 1
            if done % 200 == 0 or done == len(urls):
                print(f"checked {done}/{len(urls)}")

    status_counts = Counter(status for status, _ in results.values())
    dead = sorted(
        {checks[url] for url, (status, _) in results.items() if status in ("broken", "error")}
    )

    # Write the report before printing anything from company names - a name
    # with a non-ASCII character (Windows console defaults to cp1252) must
    # never crash the run and lose all the already-completed checking work.
    report = {
        "checked_at": None,
        "urls": {url: {"status": status, "detail": detail, "company": checks[url]} for url, (status, detail) in results.items()},
        "dead_companies": dead,
    }
    write_json_atomic(OUTPUT_PATH, report)

    def _safe_print(line: str) -> None:
        print(line.encode("ascii", "replace").decode("ascii"))

    _safe_print("\n--- by status ---")
    for status, count in status_counts.most_common():
        _safe_print(f"  {status}: {count}")

    _safe_print(f"\n--- {len(dead)} companies with at least one dead/error URL ---")
    for name in dead[:50]:
        _safe_print(f"  {name}")
    if len(dead) > 50:
        _safe_print(f"  ... and {len(dead) - 50} more (see {OUTPUT_PATH})")
    print(f"\nfull report written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
