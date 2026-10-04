"""Plan-file id for a company. Byte-for-byte the same rule as
update_jobs._snake_case (which names companies/<id>.json) so a company's
plan and its job file share a stem. Duplicated rather than imported
because update_jobs imports this package, not the other way round; Plan C
replaces both with the registry id."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query keys that are pure cache-busting/tracking noise, never part of a
# job's actual identity. Real case caught live: tikalk.com appends
# "?t=<Date.now()>" to its own job links, a fresh value every page load -
# left in, every scrape mints a new id for the SAME posting (compute_job_id
# hashes this normalized form), closing the "old" one and recreating it as
# new, forever. Deliberately a narrow, known-safe allowlist rather than
# stripping every query param: an ATS job-id query param (jobId, posting,
# gh_jid, ...) is load-bearing and must survive.
_VOLATILE_QUERY_KEYS = frozenset({
    "t", "_t", "ts", "_ts", "timestamp", "cache", "_cache", "cb", "nocache", "v", "_v",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid",
})


def normalize_job_url(url: str | None) -> str | None:
    """Identity form of a job URL: whitespace and fragment stripped, no
    trailing slash, and any cache-busting/tracking query parameter (see
    _VOLATILE_QUERY_KEYS) dropped - same rule as
    update_jobs.normalize_job_url, duplicated for the same import-direction
    reason as plan_id_for."""
    if not url:
        return None
    url = url.strip().split("#", 1)[0]
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _VOLATILE_QUERY_KEYS]
    normalized = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), urlencode(kept), ""))
    return normalized or None


def plan_id_for(company: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (company or "").lower()).strip()
    s = re.sub(r"[\s-]+", "_", s)
    return s or "unnamed_company"
