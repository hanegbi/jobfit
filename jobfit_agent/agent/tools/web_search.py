"""Free web search through ddgs. Never raises: a failed search is just no results."""

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Five research topics fan out on separate threads and each runs two queries.
# Ten at once is what gets an IP rate-limited, and a rate-limited search is a
# topic reported as "no data", so they go one at a time.
_LOCK = threading.Lock()


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
        with _LOCK:
            rows = (backend or _ddgs_backend)(query, max_results)
    except Exception as error:  # rate limits, network, library changes: degrade to "no data"
        log.warning("search failed for %r: %s", query, error)
        return []
    return [SearchHit(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in rows if r.get("href")]
