"""Scrape NVIDIA career listings (Eightfold.ai-backed jobs.nvidia.com) into a
local JSON file, then build a self-contained, searchable HTML viewer.

The site's public search widget is powered by JSON APIs that require no
authentication or session cookies:
  - GET /api/pcsx/search           -> paginated listing (10 results/page)
  - GET /api/pcsx/position_details -> full detail for one job (description,
                                       department, category, time type, ...)

Usage:
    uv run scrape_nvidia_jobs.py
"""
import concurrent.futures
import html
import json
import re
import time

import requests

BASE = "https://jobs.nvidia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Mirrors the filters from the URL the user provided:
# location=Israel, remote-eligible included, relocation excluded,
# engineering category, regular-employee job type.
SEARCH_PARAMS = {
    "domain": "nvidia.com",
    "query": "",
    "location": "Israel",
    "sort_by": "distance",
    "filter_include_remote": "1",
    "filter_include_relocation": "0",
    "filter_job_category": "engineering",
    "filter_job_type": "regular employee",
}

JSON_PATH = "nvidia_jobs.json"
HTML_TEMPLATE_PATH = "nvidia_jobs_template.html"
HTML_OUTPUT_PATH = "nvidia_jobs.html"


def fetch_all_listing_ids(session):
    ids = []
    start = 0
    total = None
    while total is None or start < total:
        params = dict(SEARCH_PARAMS, start=start)
        r = session.get(f"{BASE}/api/pcsx/search", params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()["data"]
        total = data["count"]
        positions = data["positions"]
        if not positions:
            break
        ids.extend(p["id"] for p in positions)
        start += len(positions)
        print(f"  listing page: {len(ids)}/{total} job ids collected")
        time.sleep(0.25)
    return ids, total


def fetch_detail(session, job_id, max_retries=6):
    params = {"position_id": job_id, "domain": "nvidia.com", "hl": "en"}
    for attempt in range(max_retries):
        r = session.get(f"{BASE}/api/pcsx/position_details", params=params, headers=HEADERS, timeout=20)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 0)) or (2 ** attempt)
            time.sleep(min(wait, 30))
            continue
        r.raise_for_status()
        return r.json()["data"]
    r.raise_for_status()
    return r.json()["data"]


TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw_html):
    text = re.sub(r"</p>|</li>|<br\s*/?>", "\n", raw_html or "")
    text = TAG_RE.sub("", text)
    return html.unescape(text)


def slim(detail):
    return {
        "id": detail.get("id"),
        "jobId": detail.get("displayJobId") or detail.get("atsJobId"),
        "title": detail.get("name"),
        "department": detail.get("department"),
        "category": (detail.get("efcustomTextJobFmailyGroup") or [None])[0],
        "timeType": (detail.get("efcustomTextTimeType") or [None])[0],
        "locations": detail.get("locations") or [],
        "standardizedLocations": detail.get("standardizedLocations") or [],
        "workLocationOption": detail.get("workLocationOption"),
        "locationFlexibility": detail.get("locationFlexibility"),
        "postedTs": detail.get("postedTs"),
        "creationTs": detail.get("creationTs"),
        "descriptionHtml": detail.get("jobDescription"),
        "descriptionText": html_to_text(detail.get("jobDescription")),
        "url": f"{BASE}{detail.get('positionUrl')}" if detail.get("positionUrl") else None,
    }


def main():
    session = requests.Session()

    print("Step 1/3: paging through search results...")
    ids, total = fetch_all_listing_ids(session)
    print(f"Found {total} jobs, {len(ids)} ids collected.\n")

    print("Step 2/3: fetching full details for each job...")
    raw_jobs = []
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fetch_detail, session, jid): jid for jid in ids}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            jid = futures[fut]
            done += 1
            try:
                raw_jobs.append(fut.result())
            except Exception as e:
                failed.append(jid)
                print(f"  FAILED job {jid}: {e}")
            if done % 25 == 0 or done == len(ids):
                print(f"  details: {done}/{len(ids)} fetched")

    if failed:
        print(f"\n{len(failed)} job(s) failed to fetch: {failed}")

    jobs = [slim(d) for d in raw_jobs]
    jobs.sort(key=lambda j: j.get("postedTs") or 0, reverse=True)

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(jobs)} jobs to {JSON_PATH}")

    build_html()


def build_html():
    """(Re)build nvidia_jobs.html from the existing JSON + template, no network calls."""
    print("Building self-contained HTML viewer...")
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        jobs = json.load(f)
    with open(HTML_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    jobs_json = json.dumps(jobs, ensure_ascii=False)
    output = template.replace("/*__JOBS_DATA__*/[]", jobs_json)
    with open(HTML_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"Wrote {HTML_OUTPUT_PATH} ({len(jobs)} jobs) — open it directly in a browser.")


if __name__ == "__main__":
    import sys
    if "--rebuild-only" in sys.argv:
        build_html()
    else:
        main()
