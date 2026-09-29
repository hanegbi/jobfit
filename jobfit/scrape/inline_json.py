"""Jobs inlined as JSON in a careers page.

SPA career sites (Next.js `__NEXT_DATA__`, Nuxt `__NUXT__`, any
`<script type="application/json">`) often ship the whole job list as page
data and render it as clickable rows with no <a href> at all - Lemonade's
makers.lemonade.com lists 39 roles that way. Link scraping sees nothing;
the data is right there. This finds lists of job-shaped objects in any JSON
blob in the page: items sharing a title key, a URL/link key and at least
one of location / department / employment-type keys.
"""

from __future__ import annotations

import json
import re
from typing import Iterator
from urllib.parse import urljoin, urlsplit

_SCRIPT_JSON = re.compile(r"<script[^>]*(?:id=\"__NEXT_DATA__\"|type=\"application/json\")[^>]*>(.*?)</script>", re.I | re.S)
_NUXT = re.compile(r"window\.__NUXT__\s*=\s*(\{.*?\});?\s*</script>", re.S)
TITLE_KEYS = ("title", "jobTitle", "job_title", "name", "position", "positionName", "role")
URL_KEYS = ("link", "url", "absolute_url", "absoluteUrl", "applyUrl", "apply_url", "href", "jobUrl", "job_url", "permalink")
CONTEXT_KEYS = ("location", "locations", "city", "office", "department", "departments", "team", "category",
                "employmentType", "employment_type", "type", "jobType", "country")
DESC_KEYS = ("description", "content", "body", "descriptionHtml")
MIN_ITEMS = 2


def _json_blobs(html: str) -> Iterator[object]:
    for m in _SCRIPT_JSON.finditer(html):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            continue
    for m in _NUXT.finditer(html):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            continue


def _first(d: dict, keys: tuple[str, ...]):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
        if isinstance(v, dict):
            for kk in ("name", "title", "label", "city"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    return v[kk].strip()
        if isinstance(v, list) and v and isinstance(v[0], (str, dict)):
            return _first({"x": v[0]}, ("x",))
    return None


def _looks_like_job(item: object) -> bool:
    return isinstance(item, dict) and _first(item, TITLE_KEYS) is not None and _first(item, URL_KEYS) is not None \
        and any(k in item for k in CONTEXT_KEYS)


def _job_lists(node: object) -> Iterator[list[dict]]:
    if isinstance(node, list):
        if len(node) >= MIN_ITEMS and sum(1 for x in node if _looks_like_job(x)) >= max(MIN_ITEMS, int(0.8 * len(node))):
            yield [x for x in node if _looks_like_job(x)]
            return
        for x in node:
            yield from _job_lists(x)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _job_lists(v)


def _strip_html(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def find_inline_jobs(html: str | None, base_url: str) -> list[dict]:
    """Job dicts (title, url, location, description, department,
    employment_type, posted_at) inlined in the page, deduplicated by URL.
    Only same-site or ATS-looking URLs are kept so a list of blog posts or
    partner links can't masquerade as jobs."""
    if not html or "<script" not in html:
        return []
    base_host = urlsplit(base_url).netloc.lower().removeprefix("www.")
    jobs: dict[str, dict] = {}
    for blob in _json_blobs(html):
        for items in _job_lists(blob):
            for item in items:
                url = urljoin(base_url, _first(item, URL_KEYS))
                host = urlsplit(url).netloc.lower().removeprefix("www.")
                same_site = host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)
                if not same_site and not re.search(r"greenhouse|lever\.co|comeet|ashbyhq|workable|myworkdayjobs|smartrecruiters|bamboohr|recruitee|personio|breezy", host):
                    continue
                if url in jobs:
                    continue
                jobs[url] = {
                    "title": _first(item, TITLE_KEYS),
                    "url": url,
                    "location": _first(item, ("location", "locations", "city", "office", "country")),
                    "description": _strip_html(_first(item, DESC_KEYS) or ""),
                    "department": _first(item, ("department", "departments", "team", "category")),
                    "employment_type": _first(item, ("employmentType", "employment_type", "jobType", "type")),
                    "posted_at": None,
                }
    return list(jobs.values())
