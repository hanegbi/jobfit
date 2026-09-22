"""Parse the LinkedIn Connections.csv export and index it by normalized company."""

import csv
import re
from pathlib import Path

from jobfit2 import config

_HEADER_TOKENS = {"first name", "last name", "company"}
_SUFFIXES = re.compile(
    r"\b(ltd|ltd\.|inc|inc\.|llc|group|technologies|technology|labs|systems|software|solutions|israel|the)\b",
    re.IGNORECASE,
)


def normalize_company(name: str) -> str:
    """Reduce a company name to a comparison key (lowercase, no suffixes/punct)."""
    lowered = _SUFFIXES.sub(" ", (name or "").casefold())
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _header_index(path: Path) -> int | None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for index, line in enumerate(handle):
                cells = {c.strip().lower() for c in line.split(",")}
                if _HEADER_TOKENS.issubset(cells):
                    return index
    except OSError:
        return None
    return None


def load_connections_index(path: Path = config.CONNECTIONS_CSV) -> dict[str, list[dict]]:
    """Return {normalized_company_key: [{name, url, position}, ...]}."""
    start = _header_index(path)
    if start is None:
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        lines = handle.readlines()
    reader = csv.DictReader(lines[start:])
    index: dict[str, list[dict]] = {}
    seen: dict[str, set[tuple[str, str]]] = {}
    for row in reader:
        company = re.sub(r"\s+", " ", (row.get("Company") or "").strip())
        if not company:
            continue
        key = normalize_company(company)
        if not key:
            continue
        full_name = f"{(row.get('First Name') or '').strip()} {(row.get('Last Name') or '').strip()}".strip()
        url = (row.get("URL") or "").strip() or None
        identity = (full_name.casefold(), (url or "").casefold())
        seen.setdefault(key, set())
        if identity in seen[key]:
            continue
        seen[key].add(identity)
        index.setdefault(key, []).append({
            "name": full_name,
            "url": url,
            "position": (row.get("Position") or "").strip() or None,
            "company": company,
        })
    return index


def contacts_for_company(index: dict[str, list[dict]], company_name: str) -> list[dict]:
    key = normalize_company(company_name)
    return index.get(key, [])
