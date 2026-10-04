"""Per-company research cache: one JSON file per company, refreshed after a TTL."""

import json
import re
from datetime import datetime, timedelta

from jobfit_agent.agent import config


def _path(company_id: str):
    return config.CACHE_DIR / "research" / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', company_id)}.json"


def load(company_id: str, now: str) -> dict | None:
    path = _path(company_id)
    if not path.exists():
        return None
    research = json.loads(path.read_text(encoding="utf-8"))
    age = datetime.fromisoformat(now) - datetime.fromisoformat(research["fetched_at"])
    return research if age <= timedelta(days=config.RESEARCH_TTL_DAYS) else None


def save(company_id: str, research: dict) -> None:
    path = _path(company_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(research, ensure_ascii=False, indent=2), encoding="utf-8")
