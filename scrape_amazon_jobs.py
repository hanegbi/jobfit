"""Scrape Amazon.jobs search results into JSON.

Amazon's careers search page has a clean public JSON API at
/en/search.json mirroring the page's own query params — no auth needed,
discovered by watching the page's own network request for its search.

Usage:
    uv run scrape_amazon_jobs.py
"""
import json
import re
import time
from datetime import datetime

import requests

BASE = "https://www.amazon.jobs"
SEARCH_URL = f"{BASE}/en/search.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Mirrors the filters from the URL the user provided:
# Full-Time, state=Tel Aviv, 24km radius, country=Israel, query="software engineer"
SEARCH_PARAMS = {
    "schedule_type_id[]": "Full-Time",
    "normalized_state_name[]": "Tel Aviv",
    "radius": "24km",
    "result_limit": 10,
    "sort": "relevant",
    "latitude": "",
    "longitude": "",
    "loc_group_id": "",
    "loc_query": "Israel",
    "base_query": "software engineer",
    "city": "",
    "country": "ISR",
    "region": "",
    "county": "",
    "query_options": "",
}

JSON_PATH = "amazon_jobs.json"


def html_to_text(raw_html):
    text = re.sub(r"</p>|</li>|<br\s*/?>", "\n", raw_html or "")
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_mod
    return html_mod.unescape(text)


def parse_posted_date(s):
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    try:
        return int(datetime.strptime(s, "%B %d, %Y").timestamp())
    except ValueError:
        return None


def fetch_all_jobs(session):
    jobs = []
    offset = 0
    total = None
    while total is None or offset < total:
        params = dict(SEARCH_PARAMS, offset=offset)
        r = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        total = data["hits"]
        page_jobs = data["jobs"]
        if not page_jobs:
            break
        jobs.extend(page_jobs)
        offset += len(page_jobs)
        print(f"  fetched {len(jobs)}/{total}")
        time.sleep(0.25)
    return jobs, total


def slim(job):
    full_html = "".join(filter(None, [
        job.get("description"),
        job.get("basic_qualifications"),
        job.get("preferred_qualifications"),
    ]))
    locations = []
    for loc_raw in job.get("locations") or []:
        try:
            loc_obj = json.loads(loc_raw)
            locations.append(loc_obj.get("locationNonStemming") or loc_obj.get("location"))
        except (json.JSONDecodeError, TypeError):
            pass
    if not locations and job.get("location"):
        locations = [job["location"]]

    return {
        "id": job.get("id_icims") or job.get("id"),
        "jobId": job.get("id_icims") or job.get("id"),
        "title": job.get("title"),
        "department": job.get("job_family") or job.get("job_category"),
        "category": job.get("job_category"),
        "timeType": job.get("job_schedule_type"),
        "locations": locations,
        "postedTs": parse_posted_date(job.get("posted_date")),
        "descriptionHtml": full_html,
        "descriptionText": html_to_text(full_html),
        "url": f"{BASE}{job['job_path']}" if job.get("job_path") else None,
    }


def main():
    session = requests.Session()
    print("Fetching Amazon.jobs search results...")
    raw_jobs, total = fetch_all_jobs(session)
    print(f"Found {total} jobs, fetched {len(raw_jobs)}.")

    jobs = [slim(j) for j in raw_jobs]
    jobs.sort(key=lambda j: j.get("postedTs") or 0, reverse=True)

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(jobs)} jobs to {JSON_PATH}")
    for j in jobs:
        print(f"  {j['title']}")


if __name__ == "__main__":
    main()
