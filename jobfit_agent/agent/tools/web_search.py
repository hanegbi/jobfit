"""Free web search through ddgs. Never raises: a failed search is just no results."""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


def _ddgs_backend(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS
    return DDGS().text(query, max_results=max_results)


def search(query: str, max_results: int = 5, backend=None) -> list[SearchHit]:
    try:
        rows = (backend or _ddgs_backend)(query, max_results)
    except Exception as error:  # rate limits, network, library changes: degrade to "no data"
        log.warning("search failed for %r: %s", query, error)
        return []
    return [SearchHit(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in rows if r.get("href")]
