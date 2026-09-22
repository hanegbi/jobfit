"""Scrape Google Careers search results into JSON.

Google's careers search page server-renders results into an embedded
`AF_initDataCallback({key: 'ds:1', data: [...]})` block (Google's standard
Closure/Wiz data-injection pattern) — no separate API call needed, no
authentication. Pagination is a plain `&page=N` URL param (confirmed against
the live site: 5 pages of 20 for this 100-result search, no duplicate IDs
across pages). A plain HTTP GET per page is enough.

Usage:
    uv run scrape_google_jobs.py
"""
import json
import re

import requests

SEARCH_URL_BASE = (
    "https://www.google.com/about/careers/applications/jobs/results"
    "?location=Israel&location=Tel%20Aviv%2C%20Israel&employment_type=FULL_TIME"
)
RESULTS_PER_PAGE = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

LEVEL_MAP = {2: "Mid", 3: "Advanced", 4: "Staff", 1: "Entry"}

JSON_PATH = "google_jobs.json"


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s


def html_to_text(raw_html):
    text = re.sub(r"</p>|</li>|<br\s*/?>", "\n", raw_html or "")
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_mod
    return html_mod.unescape(text)


def fetch_page(page_num, session, base_url=SEARCH_URL_BASE):
    url = base_url + (f"&page={page_num}" if page_num > 1 else "")
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    html = r.text

    blocks = re.findall(r"AF_initDataCallback\((\{.*?\})\);", html, re.S)
    ds1 = None
    for b in blocks:
        if "'ds:1'" in b or '"ds:1"' in b:
            ds1 = b
            break
    if ds1 is None and len(blocks) >= 2:
        ds1 = blocks[1]
    if ds1 is None:
        raise RuntimeError("Could not find ds:1 data block in page — Google may have changed its markup.")

    m = re.search(r"data:\s*(\[.*\]), sideChannel", ds1, re.S)
    if not m:
        raise RuntimeError("Could not extract data array from ds:1 block.")
    data = json.loads(m.group(1))
    return data[0] or []


def fetch_jobs(base_url=SEARCH_URL_BASE):
    session = requests.Session()
    seen_ids = set()
    jobs = []
    page_num = 1
    while True:
        raw_jobs = fetch_page(page_num, session, base_url)
        if not raw_jobs:
            break
        new_count = 0
        for j in raw_jobs:
            if j[0] in seen_ids:
                continue
            seen_ids.add(j[0])
            new_count += 1
        print(f"  page {page_num}: {len(raw_jobs)} jobs ({new_count} new)")
        jobs.extend(raw_jobs)
        if len(raw_jobs) < RESULTS_PER_PAGE or new_count == 0:
            break
        page_num += 1

    dedup_by_id = {}
    for j in jobs:
        dedup_by_id[j[0]] = j

    parsed = []
    for j in dedup_by_id.values():
        job_id = j[0]
        title = j[1]
        responsibilities_html = (j[3] or [None, None])[1]
        qualifications_html = (j[4] or [None, None])[1]
        company = j[7]
        locations_raw = j[9] or []
        description_html = (j[10] or [None, None])[1]
        posted_ts = (j[12] or [None])[0] if len(j) > 12 else None
        level_code = j[20] if len(j) > 20 else None

        locations = []
        for loc in locations_raw:
            city = loc[0] if len(loc) > 0 else None
            if city:
                locations.append(city)

        full_html = "".join(filter(None, [description_html, qualifications_html, responsibilities_html]))

        parsed.append({
            "id": job_id,
            "title": title,
            "company": company,
            "level": LEVEL_MAP.get(level_code, str(level_code)),
            "locations": locations,
            "postedTs": posted_ts,
            "descriptionHtml": full_html,
            "descriptionText": html_to_text(full_html),
            "url": f"https://www.google.com/about/careers/applications/jobs/results/{job_id}-{slugify(title)}",
        })
    return parsed


def main():
    jobs = fetch_jobs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(jobs)} jobs to {JSON_PATH}")
    for j in jobs:
        print(f"  [{j['level']:8s}] {j['title']}")


if __name__ == "__main__":
    main()
