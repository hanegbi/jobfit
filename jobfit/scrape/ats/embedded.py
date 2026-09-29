"""Find an ATS *embedded* in a company's own careers page.

Most Israeli career pages don't link out to a job board - they mount one
in place with a script tag (Comeet's widget, Greenhouse's embed, Ashby's
embed, a Workday/Personio/Recruitee iframe...). The visible DOM then has
no <a href> per job at all, so link-based scraping sees an empty page.
The credentials are still right there in the raw HTML though: this scans
every URL-like string (and Comeet's init config) and resolves it through
the ATS registry - one generic pass, no per-company code.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from jobfit import ats_fetchers
from jobfit.scrape.ats.base import AtsClient, AtsRegistry

_URLISH = re.compile(r"""(?:https?:)?//[A-Za-z0-9.-]+\.[a-z]{2,}[^\s"'<>\\)]*""", re.I)
_STOP = {"com", "www", "net", "org", "ltd", "inc", "llc", "the", "and", "careers", "career", "jobs", "job", "group", "company",
         "technologies", "technology", "tech", "systems", "software", "security", "labs", "lab", "formerly", "israel"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 3 and t not in _STOP}


def board_belongs_to(board: str, company_hint: str | None) -> bool:
    """A board found through a plain link must look like the company's own
    (share a name token with the company id or its career host). Mobileye's
    careers page links to Mentee Robotics' Comeet board - a partner, not
    Mobileye's jobs. No hint = no check."""
    if not company_hint:
        return True
    board_tokens = _tokens(board.split("/")[0].replace("-", " ").replace("_", " ")) | {board.split("/")[0].lower()}
    hint_tokens = _tokens(company_hint)
    return any(b in h or h in b for b in board_tokens for h in hint_tokens if len(b) >= 4 and len(h) >= 4)


def find_embedded_ats_candidates(html: str | None, registry: AtsRegistry, company_hint: str | None = None) -> list[tuple[AtsClient, str]]:
    """Every ATS board this page embeds, most plausible first: Comeet widget
    credentials from the page's own config, then link/script-referenced
    boards by frequency. A board referenced only through links must pass
    board_belongs_to(). Callers verify in order - a page can carry a dead
    Greenhouse config next to the live Ashby board it moved to (HoneyBook).
    company_hint: company id and/or career URL host, space-separated."""
    if not html:
        return []
    text = html.replace("\\/", "/")  # JSON-escaped URLs inside inline scripts
    out: list[tuple[AtsClient, str]] = []
    widget = ats_fetchers.find_comeet_widget(text)
    if widget:
        uid, token = widget
        out.append((registry.client("comeet"), f"{uid}:{token}"))
    counts: Counter[tuple[str, str]] = Counter()
    for m in _URLISH.finditer(text):
        url = m.group(0)
        if url.startswith("//"):
            url = "https:" + url
        resolved = registry.resolve(url)
        if resolved is not None:
            client, board = resolved
            counts[(client.provider, board)] += 1
    for (provider, board), _ in counts.most_common():
        if provider in ("workday", "eightfold") or board_belongs_to(board, company_hint):
            out.append((registry.client(provider), board))
    return out


def find_embedded_ats(html: str | None, registry: AtsRegistry, company_hint: str | None = None) -> tuple[AtsClient, str] | None:
    """The most plausible embedded board, or None (see find_embedded_ats_candidates)."""
    candidates = find_embedded_ats_candidates(html, registry, company_hint)
    return candidates[0] if candidates else None


def company_hint_for(company_id: str | None, career_url: str | None) -> str:
    host = urlsplit(career_url).netloc if career_url else ""
    return f"{company_id or ''} {host}"
