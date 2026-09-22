"""Reuse linkedin-match's own scraped-jobs cache, and its real scraper for gaps.

Tier order for company job data (best data first, cheapest first within a tier):
  1. linkedin-match's data/jobs_cache.json, when it holds a real scrape result.
  2. Our own techmap-URL ATS token resolution (ats_fetchers) - cheap, direct.
  3. linkedin-match's live scraper (career-page discovery + ATS detection),
     run in ITS OWN venv via subprocess, only for names missing from both above.
  4. Raw techmap CSV row (title/location/level only) - last resort.

This module implements tiers 1 and 3; pipeline.py wires in 2 and 4.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

from jobfit import config
from jobfit.connections import normalize_company

logger = logging.getLogger("jobfit.lm_bridge")

LM_DIR = Path(r"C:\Users\user\Code\linkedin-match")
LM_CACHE_PATH = LM_DIR / "data" / "jobs_cache.json"
LM_SCRAPE_SCRIPT = Path(__file__).parent / "scripts" / "scrape_via_linkedin_match.py"
MISSING_INPUT = config.ROOT / "cache" / "lm_missing_companies.json"
MISSING_OUTPUT = config.ROOT / "cache" / "lm_scraped_new.json"

_JOB_FIELDS = ("title", "location", "url", "description", "department", "employment_type", "posted_at")


def load_lm_cache() -> dict[str, dict]:
    if not LM_CACHE_PATH.exists():
        logger.warning("linkedin-match cache not found at %s", LM_CACHE_PATH)
        return {}
    try:
        return json.loads(LM_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("failed to read linkedin-match cache: %s", error)
        return {}


def index_by_normalized_name(cache: dict[str, dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for name, entry in cache.items():
        key = normalize_company(name)
        if key:
            index[key] = entry
    return index


def entry_jobs(entry: dict) -> list[dict]:
    """Normalize a linkedin-match cache entry's positions to our job dict shape."""
    jobs = []
    for pos in entry.get("positions") or []:
        title = (pos.get("title") or "").strip()
        if not title:
            continue
        jobs.append({field: pos.get(field) for field in _JOB_FIELDS})
    return jobs


def entry_has_real_jobs(entry: dict) -> bool:
    return bool(entry_jobs(entry))


def scrape_missing_via_linkedin_match(missing_names: list[str]) -> dict[str, list[dict]]:
    """Shell out to linkedin-match's own venv to scrape companies it hasn't seen.

    Returns {company_name: [job dict, ...]}. Never writes to linkedin-match's
    own data store - only reads back what its scraper found.
    """
    if not missing_names:
        return {}
    if not LM_DIR.exists() or not LM_SCRAPE_SCRIPT.exists():
        logger.warning("linkedin-match project or bridge script not found; skipping live scrape")
        return {}

    MISSING_INPUT.parent.mkdir(parents=True, exist_ok=True)
    MISSING_INPUT.write_text(json.dumps(missing_names, ensure_ascii=False), encoding="utf-8")
    if MISSING_OUTPUT.exists():
        MISSING_OUTPUT.unlink()

    logger.info("live-scraping %d companies via linkedin-match's scraper...", len(missing_names))
    result = subprocess.run(
        ["uv", "run", "python", str(LM_SCRAPE_SCRIPT), str(MISSING_INPUT), str(MISSING_OUTPUT)],
        cwd=str(LM_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    for line in (result.stderr or "").splitlines():
        logger.info("[lm-scraper] %s", line)
    if result.returncode != 0:
        logger.warning("linkedin-match scrape subprocess failed (exit %d): %s", result.returncode, result.stdout[-2000:])
        return {}
    if not MISSING_OUTPUT.exists():
        return {}

    raw = json.loads(MISSING_OUTPUT.read_text(encoding="utf-8"))
    return {name: entry_jobs(entry) for name, entry in raw.items()}
