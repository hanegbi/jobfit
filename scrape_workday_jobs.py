"""Scrape job listings from Workday-powered career sites (CXS JSON API) into
local JSON files. Covers NVIDIA's Workday site (distinct from jobs.nvidia.com,
which scrape_nvidia_jobs.py already handles) and CrowdStrike.

Workday's public CXS API needs no authentication or session cookies:
  - POST /wday/cxs/{tenant}/{site}/jobs       -> paginated listing (facet-filterable)
  - GET  /wday/cxs/{tenant}/{site}/job{path}  -> full detail for one job

Usage:
    uv run scrape_workday_jobs.py
"""
import concurrent.futures
import html
import json
import re
import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
PAGE_SIZE = 20

# Each site mirrors the filters captured from the live search URL the user gave.
SITES = [
    {
        "name": "NVIDIA (Workday)",
        "tenant": "nvidia",
        "pod": "wd5",
        "site": "NVIDIAExternalCareerSite",
        "facets": {
            "locationHierarchy1": ["2fcb99c455831013ea52bbe14cf9326c"],
            "jobFamilyGroup": ["0c40f6bd1d8f10ae43ffaefd46dc7e78"],
        },
        "json_path": "nvidia_workday_jobs.json",
    },
    {
        "name": "CrowdStrike",
        "tenant": "crowdstrike",
        "pod": "wd5",
        "site": "crowdstrikecareers",
        "facets": {
            "locationCountry": ["084562884af243748dad7c84c304d89a"],
        },
        "json_path": "crowdstrike_jobs.json",
    },
]

TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw_html):
    text = re.sub(r"</p>|</li>|<br\s*/?>", "\n", raw_html or "")
    text = TAG_RE.sub("", text)
    return html.unescape(text).strip()


def base_url(site):
    return f"https://{site['tenant']}.{site['pod']}.myworkdayjobs.com"


def wday_prefix(site):
    return f"{base_url(site)}/wday/cxs/{site['tenant']}/{site['site']}"


def fetch_all_listings(session, site):
    listings = []
    offset = 0
    total = None
    url = f"{wday_prefix(site)}/jobs"
    while total is None or offset < total:
        body = {"appliedFacets": site["facets"], "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        r = session.post(url, json=body, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        if total is None:
            total = data.get("total", 0)
        postings = data.get("jobPostings") or []
        if not postings:
            break
        listings.extend(postings)
        offset += len(postings)
        print(f"  listing page: {len(listings)}/{total} jobs collected")
        time.sleep(0.25)
    return listings, total


def fetch_detail(session, site, posting, max_retries=6):
    url = f"{wday_prefix(site)}{posting['externalPath']}"
    for attempt in range(max_retries):
        r = session.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 0)) or (2 ** attempt)
            time.sleep(min(wait, 30))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def slim(site, posting, detail):
    info = detail.get("jobPostingInfo") or {}
    req_ids = posting.get("bulletFields") or []
    desc_html = info.get("jobDescription", "")
    return {
        "id": info.get("id") or posting.get("externalPath"),
        "jobId": req_ids[0] if req_ids else info.get("jobReqId"),
        "title": info.get("title") or posting.get("title"),
        "location": info.get("location") or posting.get("locationsText"),
        "country": info.get("country", {}).get("descriptor") if isinstance(info.get("country"), dict) else info.get("country"),
        "timeType": info.get("timeType"),
        "postedOn": info.get("postedOn") or posting.get("postedOn"),
        "startDate": info.get("startDate"),
        "descriptionHtml": desc_html,
        "descriptionText": html_to_text(desc_html),
        "url": base_url(site) + (info.get("externalUrl") or posting["externalPath"]) if not (info.get("externalUrl") or "").startswith("http") else info.get("externalUrl"),
    }


def scrape_site(session, site):
    print(f"\n=== {site['name']} ===")
    print("Step 1/2: paging through search results...")
    listings, total = fetch_all_listings(session, site)
    print(f"Found {total} jobs, {len(listings)} listed.")

    print("Step 2/2: fetching full details for each job...")
    jobs = []
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fetch_detail, session, site, p): p for p in listings}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            posting = futures[fut]
            done += 1
            try:
                detail = fut.result()
                jobs.append(slim(site, posting, detail))
            except Exception as e:
                failed.append(posting.get("title"))
                print(f"  FAILED {posting.get('title')}: {e}")
            if done % 10 == 0 or done == len(listings):
                print(f"  details: {done}/{len(listings)} fetched")

    if failed:
        print(f"{len(failed)} job(s) failed to fetch: {failed}")

    jobs.sort(key=lambda j: j.get("title") or "")
    with open(site["json_path"], "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(jobs)} jobs to {site['json_path']}")
    return jobs


def main():
    session = requests.Session()
    for site in SITES:
        scrape_site(session, site)


if __name__ == "__main__":
    main()
