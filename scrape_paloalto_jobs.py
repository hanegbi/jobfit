"""Scrape Palo Alto Networks (Radancy-powered careers site) search results.

Two-step scrape:
  1. GET /en/search-jobs/results (an internal Radancy AJAX endpoint, found by
     monkey-patching fetch/XHR while clicking "Next" in the browser) returns
     an HTML fragment of job cards per page — no public JSON listing API here.
  2. Each job detail page embeds a clean schema.org JobPosting JSON-LD block
     (same pattern as NVIDIA's site) with the full description.

Usage:
    uv run scrape_paloalto_jobs.py
"""
import concurrent.futures
import json
import re
import time

import requests

BASE = "https://jobs.paloaltonetworks.com"
RESULTS_URL = f"{BASE}/en/search-jobs/results"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"{BASE}/en/search-jobs/Israel/47263/2/294640/31x5/34x75/50/2",
}

RECORDS_PER_PAGE = 15

# Mirrors the search state captured from the live "Israel" search on the site.
BASE_PARAMS = {
    "ActiveFacetID": "0",
    "RecordsPerPage": str(RECORDS_PER_PAGE),
    "Distance": "50",
    "RadiusUnitType": "0",
    "Keywords": "",
    "Location": "Israel",
    "Latitude": "31.50000",
    "Longitude": "34.75000",
    "ShowRadius": "False",
    "IsPagination": "False",
    "CustomFacetName": "",
    "FacetTerm": "",
    "FacetType": "0",
    "SearchResultsModuleName": "Section 29 - Search Results",
    "SearchFiltersModuleName": "Section 29 - Search Filters",
    "SortCriteria": "0",
    "SortDirection": "0",
    "SearchType": "1",
    "LocationType": "2",
    "LocationPath": "294640",
    "OrganizationIds": "47263",
    "PostalCode": "",
    "ResultsType": "0",
}

JSON_PATH = "paloalto_jobs.json"


def html_to_text(raw_html):
    text = re.sub(r"</p>|</li>|<br\s*/?>", "\n", raw_html or "")
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_mod
    return html_mod.unescape(text)


def parse_listing_page(html):
    jobs = []
    for li in re.findall(r'<li class="section29__search-results-li">.*?</li>\s*</li>', html, re.S):
        pass
    # Simpler: iterate over each result link block directly.
    for m in re.finditer(
        r'<a class="section29__search-results-link" href="([^"]+)"[^>]*data-job-id="(\d+)">'
        r'\s*<h2 class="section29__search-results-job-title">([^<]+)</h2>'
        r'.*?<span class="section29__result-location[^"]*">([^<]+)</span>'
        r'(?:.*?<span class="section29__result-category">\s*<span>([^<]*)</span>)?',
        html, re.S,
    ):
        href, job_id, title, location, category = m.groups()
        jobs.append({
            "id": job_id,
            "title": title.strip(),
            "url": BASE + href,
            "location": location.strip(),
            "category": (category or "").strip(),
        })
    return jobs


def fetch_all_listings(session):
    all_jobs = []
    total_results = None
    page = 1
    while True:
        params = dict(BASE_PARAMS, CurrentPage=str(page))
        r = session.get(RESULTS_URL, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        results_html = data.get("results") or ""
        if total_results is None:
            m = re.search(r'([\d,]+) search results', results_html)
            if m:
                total_results = int(m.group(1).replace(",", ""))
        page_jobs = parse_listing_page(results_html)
        if not page_jobs:
            break
        all_jobs.extend(page_jobs)
        print(f"  page {page}: {len(all_jobs)}/{total_results} jobs collected")
        if total_results and len(all_jobs) >= total_results:
            break
        page += 1
        time.sleep(0.3)
    return all_jobs, total_results


def fetch_detail(session, job):
    r = session.get(job["url"], headers=HEADERS, timeout=20)
    r.raise_for_status()
    html = r.text
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if not m:
        return None
    data = json.loads(m.group(1))

    locations = []
    for loc in data.get("jobLocation") or []:
        addr = loc.get("address", {})
        city = addr.get("addressLocality")
        if city:
            locations.append(f"{city}, {addr.get('addressCountry', '')}".strip(", "))
    if not locations:
        locations = [job["location"]]

    posted_ts = None
    dp = data.get("datePosted")
    if dp:
        try:
            import datetime
            y, mo, d = dp.split("-")
            posted_ts = int(datetime.datetime(int(y), int(mo), int(d)).timestamp())
        except (ValueError, TypeError):
            pass

    desc_html = data.get("description", "")
    return {
        "id": job["id"],
        "jobId": data.get("identifier") or job["id"],
        "title": data.get("title") or job["title"],
        "department": job["category"],
        "category": job["category"],
        "timeType": "Full time",
        "locations": locations,
        "postedTs": posted_ts,
        "descriptionHtml": desc_html,
        "descriptionText": html_to_text(desc_html),
        "url": job["url"],
    }


def main():
    session = requests.Session()
    print("Step 1/2: paging through search results...")
    listings, total = fetch_all_listings(session)
    print(f"Found {total} jobs, {len(listings)} listed.\n")

    print("Step 2/2: fetching full details for each job...")
    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fetch_detail, session, j): j for j in listings}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            try:
                result = fut.result()
                if result:
                    jobs.append(result)
            except Exception as e:
                print(f"  FAILED {futures[fut]['title']}: {e}")
            if done % 10 == 0 or done == len(listings):
                print(f"  details: {done}/{len(listings)} fetched")

    jobs.sort(key=lambda j: j.get("postedTs") or 0, reverse=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(jobs)} jobs to {JSON_PATH}")
    for j in jobs:
        print(f"  {j['title']}")


if __name__ == "__main__":
    main()
