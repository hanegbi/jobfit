"""Job family classification: title keyword rules first, then a JD-text
fallback for generic titles, never a model call.

This is the one function the scrape pipeline and the backfill script both
call - see classify_job()'s own docstring for the contract. Scoring never
calls this; it only ever reads the family/canonical_title/confidence
columns classify_job() already wrote to the store.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from typing import NamedTuple

from jobfit.ats_scorer.jd_extractor import _split_sections
from jobfit.ats_scorer.taxonomy import CANONICAL_TITLES_PATH, ROLE_FAMILIES_PATH, load_role_families

# A bare, undifferentiated title - the title alone can't say which family,
# because "Software Engineer" is backend/frontend/fullstack/ml/embedded/
# anything. Real data forced this: run title-only against the 12,823 active
# jobs in the store and "Senior Software Engineer" (43) + "Software Engineer"
# (37) - the single most common real tech title in the whole corpus - were
# both entirely unclassified, because no family's keyword list has a bare
# catch-all (correctly - there's nothing to disambiguate from the title).
_GENERIC_TITLE_RE = re.compile(
    r"^(senior|sr\.?|junior|jr\.?|lead|principal|staff|\s)*"
    r"(software engineer|swe|engineer|developer|programmer|"
    r"software developer|dev)"
    r"(\s*(i{1,3}|iv|v|[0-9]+))?$",
    re.IGNORECASE,
)


class JobClassification(NamedTuple):
    family: str | None
    canonical_title: str | None
    confidence: str  # "title" | "jd_fallback" | "unknown"
    taxonomy_version: str


@lru_cache(maxsize=1)
def load_canonical_titles() -> dict[str, list[str]]:
    """{family: [canonical title, ...]}, primary form first."""
    return json.loads(CANONICAL_TITLES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def taxonomy_version() -> str:
    """A content hash of the taxonomy data files classify_job() depends on.

    Not a hand-maintained version string - nobody has to remember to bump a
    number. Backfill compares a stored job's taxonomy_version against this;
    any edit to either file changes the hash, which is exactly "the
    taxonomy changed, this row needs reclassifying."
    """
    parts = [ROLE_FAMILIES_PATH.read_bytes(), CANONICAL_TITLES_PATH.read_bytes()]
    return hashlib.sha256(b"\x00".join(parts)).hexdigest()[:12]


def _is_generic_title(title: str) -> bool:
    cleaned = " ".join((title or "").split())
    return bool(_GENERIC_TITLE_RE.match(cleaned))


def classify_job(title: str | None, description: str | None = None) -> JobClassification:
    """Classify a job into a family, from its title first, its own
    description's responsibility text only when the title is too generic
    to say anything on its own.

    Pure function: no I/O beyond reading the (cached, lru_cache'd) taxonomy
    data files on first call, no network, no DB. Safe to call from the live
    scrape path (jobfit/scrape/service.py, right after enrichment) and from
    a backfill script iterating stored (title, description) pairs with no
    network at all - same function, same result, either way.

    Args:
        title: The job's title.
        description: The job's description, if any. Only consulted when
            the title alone is generic (see _GENERIC_TITLE_RE).

    Returns:
        A JobClassification. family/canonical_title are None when nothing
        matched - never a guess, per the "unknown is terminal" rule.
    """
    title = title or ""
    families = load_role_families()
    version = taxonomy_version()

    family = families.classify(title)
    if family:
        canonical = load_canonical_titles().get(family, [None])[0]
        return JobClassification(family, canonical, "title", version)

    if description and _is_generic_title(title):
        sections = _split_sections(description)
        # Responsibility text first (what the role actually DOES, the most
        # reliable fallback signal); the rest of the description only if
        # that section itself gave nothing, since a long JD often mentions
        # other teams/technologies in passing that would misclassify it.
        responsibility_text = " ".join(sections.get("responsibility", []))
        family = families.classify(responsibility_text) if responsibility_text else None
        if not family:
            family = families.classify(description)
        if family:
            canonical = load_canonical_titles().get(family, [None])[0]
            return JobClassification(family, canonical, "jd_fallback", version)

    return JobClassification(None, None, "unknown", version)
