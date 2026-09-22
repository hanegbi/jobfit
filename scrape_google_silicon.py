"""Scrape the Google 'silicon OR hardware' Israel search (reuses scrape_google_jobs.py)."""
import json

from scrape_google_jobs import fetch_jobs

URL = (
    "https://www.google.com/about/careers/applications/jobs/results"
    '?e=72477625&location=Israel&q=%22silicon%22%20OR%20%22hardware%22'
)

if __name__ == "__main__":
    jobs = fetch_jobs(URL)
    with open("google_jobs_silicon.json", "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(jobs)} jobs to google_jobs_silicon.json")
