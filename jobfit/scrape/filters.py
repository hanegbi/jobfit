"""LinkFilters decide, per candidate link, "job posting or not". Each
filter returns a final Verdict or None (no opinion); FilterChain runs
them in order and DENIES BY DEFAULT - a candidate no filter accepts is
rejected. Hard rejects (denylist, href markers, the audit's reject list,
category overviews) come first, then the plan's own patterns, then the
generic evidence filters that act as the drift safety net."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from urllib.parse import urlsplit

from pydantic import BaseModel

from jobfit.scrape.models import Candidate

NAV_DENYLIST = re.compile(
    # Anchored: a whole anchor text that is exactly one of these is site
    # furniture, never a posting. Each addition here was found in the store
    # as an open "job" - 118 x "About Us", 47 x "Terms", 21 x "Careers".
    r"^(home|about( us)?|contact( us)?|privacy( policy)?|"
    r"terms( (of (use|service)|and conditions|& conditions))?|cookies?( policy)?|"
    r"sign ?(in|up)|log ?(in|out)|careers?|jobs?|open (roles|positions)|join us|work with us|"
    r"register|blog|news|press|resources?|white papers?|case stud(y|ies)|"
    r"investors?|sustainability|diversity|benefits?|life at|culture|our (team|story|values)|"
    r"locations?|offices?|leadership|board|help|faq|support|search( jobs?)?|filter|sort by|share|"
    r"apply( now| today)?|view all|see all|view (open )?positions?|view listing|browse all|"
    r"\+? ?view more positions?|learn more|read more( ?>)?|back to|skip to|menu|toggle|close|"
    r"let'?s talk|follow (us|gett .*)|submit (cv|resume)|eeo is the law|job search|"
    r"linkedin|facebook|twitter|instagram|youtube)$",
    re.I,
)
# A nav link whose label carries the page's tagline: "Careers Join the team
# building the operating system for working dogs.", "About Us Discover the
# DogBase story", "Career Opportunities". NAV_DENYLIST is anchored and so only
# catches the bare label, which left ~220 of these stored as jobs.
#
# A bare prefix match would eat real roles that start the same way - "Support
# Engineer", "Press Officer", "News Editor", "Careers Advisor" - so the prefix
# alone is not enough: the title must ALSO contain no word that names a job.
_NAV_PREFIX_RE = re.compile(
    r"^(about( us| the position)?|careers?|blog|news|press|resources?|terms|privacy|cookies?|"
    r"contact( us)?|sign ?(in|up)|log ?(in|out)|learn more|read more|view all|see all|"
    r"home( page)?|our (story|team|values|mission)|who we are|join us)\b",
    re.I,
)
# Words that make a string a job rather than a section of a website. Kept
# deliberately narrow: these name a PERSON who does something.
_ROLE_WORD_RE = re.compile(
    r"\b(engineer|engineering|developer|programmer|architect|scientist|researcher|analyst|"
    r"manager|director|head|lead|leader|chief|officer|president|partner|executive|"
    r"designer|writer|editor|copywriter|marketer|recruiter|accountant|bookkeeper|controller|"
    r"counsel|attorney|lawyer|paralegal|advisor|adviser|consultant|coach|trainer|instructor|"
    r"specialist|coordinator|administrator|technician|operator|mechanic|electrician|"
    r"representative|agent|associate|assistant|intern|student|apprentice|"
    r"sdet|devops|sre|qa|pmm?|cto|cfo|ceo|coo|ciso|vp)\b",
    re.I,
)


def looks_like_site_furniture(text: str | None) -> bool:
    """True for a nav link whose label ran into the page's tagline.

    The whole-label case is NAV_DENYLIST's job; this is the one that carries
    extra words. Both conditions are required, because the prefixes are also
    how several real titles begin.

    Applied to a job's FINAL title, never to a candidate link's anchor text.
    Several sites label every job link with a CTA - speedata's say "About the
    position", citrusx's "Learn More & Apply" - and the real title is
    recovered from the job's own page afterwards. Judging the anchor text
    rejected those companies' entire listings; the replay suite caught it.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned or not _NAV_PREFIX_RE.match(cleaned):
        return False
    return not _ROLE_WORD_RE.search(cleaned)


_FORM_TOKEN_RE = re.compile(r"^\[#|#\]$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+\??$")
_URL_TEXT_RE = re.compile(r"^(https?://|www\.)", re.I)

# Substrings that mark a link destination as never a job posting regardless
# of its text - office links to Google Maps; a shared site-wide footer's
# docs/blog/legal/press links whose anchor text ("OpenTelemetry", "Code
# Governance & Compliance" - both caught live) reads like a plausible title.
NON_JOB_LINK_HREF_MARKERS = (
    "google.com/maps", "maps.google.com", "goo.gl/maps",
    "/docs/", "/documentation/", "/blog/", "/resources/", "/resource-library/",
    "/legal/", "/trust-center/", "/security-center/", "/press/", "/newsroom/",
    "/case-studies/", "/case-study/", "/webinars/", "/community/", "/partners/",
)


class Verdict(BaseModel):
    accept: bool
    filter_name: str
    reason: str


class LinkFilter(ABC):
    name: str = "filter"

    @abstractmethod
    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None: ...

    def _reject(self, reason: str) -> Verdict:
        return Verdict(accept=False, filter_name=self.name, reason=reason)

    def _accept(self, reason: str) -> Verdict:
        return Verdict(accept=True, filter_name=self.name, reason=reason)


class DenylistFilter(LinkFilter):
    name = "denylist"

    @staticmethod
    def text_ok(text: str) -> bool:
        text = (text or "").strip()
        if not (8 <= len(text) <= 120):
            return False
        if NAV_DENYLIST.match(text):
            return False
        if _FORM_TOKEN_RE.search(text) or _EMAIL_RE.match(text):
            return False
        if _URL_TEXT_RE.match(text):
            return False
        # Hebrew words carry no vowels, so they run shorter than the Latin
        # 3-letter floor (Elbit Systems Sigmabit's site is entirely Hebrew).
        if not (re.search(r"[A-Za-z]{3,}", text) or re.search(r"[א-ת]{2,}", text)):
            return False
        return True

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        return None if self.text_ok(candidate.text) else self._reject("nav/boilerplate text")


class HrefMarkerFilter(LinkFilter):
    name = "href_marker"

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        href = candidate.href.lower()
        for marker in NON_JOB_LINK_HREF_MARKERS:
            if marker in href:
                return self._reject(f"href contains {marker!r}")
        return None


class RejectListFilter(LinkFilter):
    name = "reject_list"

    def __init__(self, patterns: list[str]):
        self.patterns = [re.compile(p) for p in patterns]

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        for pattern in self.patterns:
            if pattern.search(candidate.href):
                return self._reject(f"matches reject pattern {pattern.pattern!r}")
        return None


def _parent_segments(url: str) -> tuple[str, ...]:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return tuple(segments[:-1])


class CategoryPrefixFilter(LinkFilter):
    """Reject a department/category overview link whose parent directory
    is a strict prefix of a sibling candidate's parent directory (the
    overview .../careers/engineering/all vs its postings
    .../careers/engineering/<id>/<slug>/all). An empty parent (flat
    query-string schemes like Check Point's index.php?joborderid=N) is
    never "more general" than anything."""
    name = "category_prefix"

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        mine = _parent_segments(candidate.href)
        if not mine:
            return None
        for other in batch:
            if other.index == candidate.index:
                continue
            theirs = _parent_segments(other.href)
            if len(mine) < len(theirs) and theirs[: len(mine)] == mine:
                return self._reject("category overview of a sibling posting")
        return None


class PlanPatternFilter(LinkFilter):
    name = "plan_pattern"

    def __init__(self, include_url: str | None, exclude_url: list[str], explicit_accept: list[str]):
        self.include = re.compile(include_url) if include_url else None
        self.excludes = [re.compile(p) for p in exclude_url]
        self.explicit = set(explicit_accept)

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        for pattern in self.excludes:
            if pattern.search(candidate.href):
                return self._reject(f"plan exclude {pattern.pattern!r}")
        if self.include is not None and self.include.search(candidate.href):
            return self._accept("plan include_url")
        if candidate.href in self.explicit:
            return self._accept("plan explicit_accept")
        return None


class UrlShapeClusterFilter(LinkFilter):
    """With an expected shape (from a plan): accept candidates of that
    shape. Without one (rules-only): reject a candidate whose shape is a
    singleton in the batch AND lacks a job-url hint - the flat marketing
    slug next to a cluster of /careers/<slug> links."""
    name = "url_shape"

    def __init__(self, expected_shape: str | None):
        self.expected_shape = expected_shape

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        if self.expected_shape is not None:
            return self._accept("matches plan url_shape") if candidate.href_shape == self.expected_shape else None
        counts = Counter(c.href_shape for c in batch)
        if counts[candidate.href_shape] == 1 and not candidate.has_job_url_hint and len(batch) >= 3:
            return self._reject("singleton url shape without a job hint")
        return None


class EvidenceThresholdFilter(LinkFilter):
    name = "evidence"
    SIGNALS = ("same_host", "under_career_path", "has_job_url_hint", "role_family", "siblings")

    def __init__(self, min_signals: int = 2, reject_chrome: bool = True):
        self.min_signals = min_signals
        self.reject_chrome = reject_chrome

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        if candidate.in_chrome and self.reject_chrome:
            return self._reject("inside nav/header/footer")
        signals = sum([
            candidate.same_host, candidate.under_career_path, candidate.has_job_url_hint,
            candidate.role_family is not None, candidate.sibling_anchor_count >= 3,
        ])
        if signals >= self.min_signals:
            return self._accept(f"{signals} positive signals")
        return None


class FilterChain:
    def __init__(self, filters: list[LinkFilter]):
        self.filters = filters

    def run(self, batch: list[Candidate]) -> tuple[list[Candidate], list[tuple[Candidate, Verdict]]]:
        accepted: list[Candidate] = []
        rejected: list[tuple[Candidate, Verdict]] = []
        for candidate in batch:
            verdict = None
            for f in self.filters:
                verdict = f.accept(candidate, batch)
                if verdict is not None:
                    break
            if verdict is None:
                verdict = Verdict(accept=False, filter_name="chain", reason="no filter accepted this link")
            (accepted if verdict.accept else rejected).append(candidate if verdict.accept else (candidate, verdict))
        return accepted, rejected


def legacy_listing_chain() -> FilterChain:
    """Exactly what the pre-plan heuristics accepted: reject denylisted
    text, non-job href markers and category overviews; accept everything
    else. Used by the two legacy listing entry points until Group B wires
    the plan-driven chain, and by RulesPlanClassifier as its base."""
    return FilterChain([
        DenylistFilter(), HrefMarkerFilter(), CategoryPrefixFilter(),
        EvidenceThresholdFilter(min_signals=0, reject_chrome=False),
    ])
