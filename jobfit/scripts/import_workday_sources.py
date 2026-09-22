"""One-off import of Workday-sourced job dumps (from scrape_workday_jobs.py at
the repo root) into jobfit2's companies/*.json store, using update_jobs.py's
own diff/score/save logic so the result is indistinguishable from a normal
incremental update - then re-aggregates jobs_v2.json.

Existing jobs no longer present in the Workday dump are marked "closed" (not
deleted), matching the rest of the app's diff semantics.

Usage:
    uv run python -m jobfit2.scripts.import_workday_sources
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit2 import config, cv  # noqa: E402
from jobfit2.scripts import update_jobs as uj  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

SOURCES = [
    (
        "Nvidia",
        ROOT / "nvidia_workday_jobs.json",
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        "?locationHierarchy1=2fcb99c455831013ea52bbe14cf9326c&jobFamilyGroup=0c40f6bd1d8f10ae43ffaefd46dc7e78",
    ),
    (
        "CrowdStrike",
        ROOT / "crowdstrike_jobs.json",
        "https://crowdstrike.wd5.myworkdayjobs.com/en-US/crowdstrikecareers/jobs"
        "?locationCountry=084562884af243748dad7c84c304d89a",
    ),
]


def _to_fetched_jobs(raw_jobs: list[dict]) -> list[dict]:
    return [
        {
            "title": j.get("title"),
            "location": j.get("location"),
            "url": j.get("url"),
            "description": j.get("descriptionHtml") or j.get("descriptionText"),
        }
        for j in raw_jobs
        if j.get("title")
    ]


def main() -> None:
    profiles = cv.load_profiles()
    for company, json_path, career_url in SOURCES:
        raw_jobs = json.loads(json_path.read_text(encoding="utf-8"))
        fetched = _to_fetched_jobs(raw_jobs)
        record, new_count, closed_count = uj.diff_and_update(company, career_url, fetched, profiles, rescore_all=True)
        uj.save_company_file(company, record)
        print(f"{company}: {len(record['jobs'])} jobs on file ({new_count} new, {closed_count} closed)")

    count = uj.aggregate_to_jobs_v2()
    print(f"aggregated {count} jobs total into {config.JOBS_OUTPUT_JSON}")


if __name__ == "__main__":
    main()
