"""Download and cache the public techmap job-listing CSVs, keyed by category."""

import csv
import io
import logging
from pathlib import Path

import requests

from jobfit import config

logger = logging.getLogger("jobfit.techmap")


def download_category(category: str, session: requests.Session, force: bool = False) -> str:
    """Download one category CSV, caching it locally under cache/techmap."""
    config.TECHMAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TECHMAP_CACHE_DIR / f"{category}.csv"
    if path.exists() and not force:
        return path.read_text(encoding="utf-8-sig")
    url = config.TECHMAP_RAW_BASE.format(category=category)
    response = session.get(url, timeout=30)
    response.raise_for_status()
    path.write_text(response.text, encoding="utf-8")
    return response.text


def parse_category_csv(text: str, category: str) -> list[dict]:
    """Parse one techmap category CSV into raw row dicts tagged with its category."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = []
    for row in reader:
        rows.append(
            {
                "company": (row.get("company") or "").strip(),
                "industry": (row.get("category") or "").strip() or None,
                "size": (row.get("size") or "").strip() or None,
                "title": (row.get("title") or "").strip(),
                "level": (row.get("level") or "").strip() or None,
                "location": (row.get("city") or "").strip() or None,
                "url": (row.get("url") or "").split("?")[0] or None,
                "posted_at": (row.get("updated") or "").strip() or None,
                "function": category,
            }
        )
    return [r for r in rows if r["company"] and r["title"]]


def load_all_rows(session: requests.Session, force: bool = False) -> list[dict]:
    """Download (or read from cache) every category and return all raw job rows."""
    rows: list[dict] = []
    for category in config.TECHMAP_CATEGORIES:
        text = download_category(category, session, force=force)
        parsed = parse_category_csv(text, category)
        rows.extend(parsed)
        logger.info("techmap: %s -> %d rows", category, len(parsed))
    return rows
