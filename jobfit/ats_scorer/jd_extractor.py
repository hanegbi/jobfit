"""Rules-based extraction of structured JobRequirements from raw job
description text. No LLM calls; everything here is regex and keyword
matching against the taxonomy/role-family data files.
"""

import re

from jobfit.ats_scorer.models import JobRequirements, Requirement, RequirementKind, Seniority
from jobfit.ats_scorer.patterns import CERTIFICATION_RE, DEGREE_RE, LANGUAGE_RE, LOCATION_RE, YEARS_TOTAL_RE
from jobfit.ats_scorer.taxonomy import load_role_families, load_skills_taxonomy

_MUST_HAVE_HEADERS = ("requirements", "qualifications", "what you'll need", "what you need",
                      "what we're looking for", "minimum qualifications")
_NICE_TO_HAVE_HEADERS = ("nice to have", "nice-to-have", "advantage", "advantages", "bonus",
                         "bonus points", "plus", "preferred qualifications", "preferred")
_RESPONSIBILITY_HEADERS = ("responsibilities", "what you'll do", "what you will do",
                           "about the role", "the role", "role overview", "your role")

_SOFTENER_RE = re.compile(
    r"\b(advantage|plus|nice to have|preferred|bonus|familiarity with|familiar with|"
    r"is a plus|ideally|desired|good to have)\b",
    re.IGNORECASE,
)
_HARD_WORD_RE = re.compile(
    r"\b(must|required|require|requires|at least|minimum|min\.|proven|mandatory)\b",
    re.IGNORECASE,
)

_YEARS_BULLET_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:experience\s+(?:with|in)\s+)?([a-zA-Z][\w\s./#+-]{2,40}?)"
    r"(?:experience)?[.,;]|"
    r"(\d{1,2})\s*-\s*\d{1,2}\s*years?\s+(?:of\s+)?(?:experience\s+(?:with|in)\s+)?([a-zA-Z][\w\s./#+-]{2,40}?)[.,;]",
    re.IGNORECASE,
)

_SENIORITY_TITLE_PATTERNS: list[tuple[Seniority, re.Pattern]] = [
    (Seniority.HEAD, re.compile(r"\bhead of\b", re.IGNORECASE)),
    (Seniority.MANAGER, re.compile(r"\b(manager|vp|vice president|cto|director)\b", re.IGNORECASE)),
    (Seniority.PRINCIPAL, re.compile(r"\bprincipal\b", re.IGNORECASE)),
    (Seniority.STAFF, re.compile(r"\bstaff\b", re.IGNORECASE)),
    (Seniority.LEAD, re.compile(r"\b(lead|tech lead|team lead)\b", re.IGNORECASE)),
    (Seniority.SENIOR, re.compile(r"\bsenior\b|\bsr\.?\b", re.IGNORECASE)),
    (Seniority.JUNIOR, re.compile(r"\bjunior\b|\bjr\.?\b|\bentry.level\b|\bgraduate\b", re.IGNORECASE)),
]

_BULLET_LINE_RE = re.compile(r"^\s*[-*••●‣⁃]\s*|^\s*\d+[.)]\s*")


def _split_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines()]


def _is_header(line: str) -> str | None:
    """Return the header category ("must", "nice", "responsibility") if
    this line looks like a section header, else None."""
    stripped = line.strip().strip(":").strip().lower()
    if not stripped or len(stripped) > 60:
        return None
    if any(stripped == h or stripped.startswith(h) for h in _MUST_HAVE_HEADERS):
        return "must"
    if any(stripped == h or stripped.startswith(h) for h in _NICE_TO_HAVE_HEADERS):
        return "nice"
    if any(stripped == h or stripped.startswith(h) for h in _RESPONSIBILITY_HEADERS):
        return "responsibility"
    return None


def _split_sections(text: str) -> dict[str, list[str]]:
    """Split JD text into must/nice/responsibility/other line buckets by
    scanning for header lines. Lines before the first recognized header go
    to "other" (used for title/domain context, not as requirements)."""
    sections: dict[str, list[str]] = {"must": [], "nice": [], "responsibility": [], "other": []}
    current = "other"
    for line in _split_lines(text):
        if not line:
            continue
        header = _is_header(line)
        if header:
            current = header
            continue
        sections[current].append(line)
    return sections


def _clean_bullet(line: str) -> str:
    return _BULLET_LINE_RE.sub("", line).strip()


def _extract_years_from_bullet(bullet: str) -> int | None:
    match = _YEARS_BULLET_RE.search(bullet)
    if not match:
        return None
    for group in (match.group(1), match.group(3)):
        if group:
            return int(group)
    return None


def classify_requirement_kind(bullet: str, canonical_skill: str | None) -> RequirementKind:
    """Return the RequirementKind implied by a bullet's own content.

    Args:
        bullet: The cleaned bullet text.
        canonical_skill: A canonical skill name already found in the
            bullet, if any.

    Returns:
        The most specific RequirementKind the bullet's content implies.
    """
    if DEGREE_RE.search(bullet):
        return RequirementKind.DEGREE
    if CERTIFICATION_RE.search(bullet):
        return RequirementKind.CERTIFICATION
    if LANGUAGE_RE.search(bullet):
        return RequirementKind.LANGUAGE
    if LOCATION_RE.search(bullet):
        return RequirementKind.LOCATION
    if canonical_skill:
        return RequirementKind.SKILL
    if _extract_years_from_bullet(bullet) is not None or re.search(r"\byears?\b", bullet, re.IGNORECASE):
        return RequirementKind.YEARS
    return RequirementKind.DOMAIN


def _bullet_to_requirements(bullet: str) -> list[Requirement]:
    """Turn one cleaned bullet into zero or more Requirement objects - a
    bullet can name multiple skills, each becoming its own Requirement so
    matching/coverage can be computed per skill."""
    taxonomy = load_skills_taxonomy()
    skills_found = taxonomy.find_in_text(bullet)
    years = _extract_years_from_bullet(bullet)

    if skills_found:
        return [
            Requirement(
                kind=RequirementKind.SKILL, text=bullet, canonical=skill,
                years=years if len(skills_found) == 1 else None,
            )
            for skill in skills_found
        ]

    kind = classify_requirement_kind(bullet, None)
    return [Requirement(kind=kind, text=bullet, canonical=None, years=years if kind == RequirementKind.YEARS else None)]


def _classify_bullet_must_or_nice(bullet: str) -> str:
    """Return "must" or "nice" for one requirements-section bullet, per the
    hard-word/softener rule: hard words always win over a softener."""
    if _HARD_WORD_RE.search(bullet):
        return "must"
    if _SOFTENER_RE.search(bullet):
        return "nice"
    return "must"


def _infer_seniority(title: str, required_years_total: int | None) -> Seniority:
    for level, pattern in _SENIORITY_TITLE_PATTERNS:
        if pattern.search(title):
            return level
    if required_years_total is not None:
        if required_years_total <= 2:
            return Seniority.JUNIOR
        if required_years_total <= 5:
            return Seniority.MID
        return Seniority.SENIOR
    return Seniority.MID


def _extract_required_years_total(sections: dict[str, list[str]]) -> int | None:
    found = []
    for bucket in ("must", "other", "responsibility"):
        for line in sections[bucket]:
            for match in YEARS_TOTAL_RE.finditer(line):
                value = next((g for g in match.groups() if g), None)
                if value:
                    found.append(int(value))
    return max(found) if found else None


def extract_job_requirements(text: str, title: str | None = None) -> JobRequirements:
    """Extract structured JobRequirements from raw job description text.

    Args:
        text: The full job description body text.
        title: The job's title, if known separately from the body; falls
            back to the first non-empty line of text when omitted.

    Returns:
        A JobRequirements built entirely from patterns found in the text -
        nothing is invented.
    """
    text = text or ""
    lines = _split_lines(text)
    resolved_title = title or next((ln for ln in lines if ln.strip()), "")

    sections = _split_sections(text)

    must_have: list[Requirement] = []
    nice_to_have: list[Requirement] = []
    for line in sections["must"]:
        bullet = _clean_bullet(line)
        if not bullet:
            continue
        # A softener inside a must-have-labeled section (e.g. "familiarity
        # with X is a plus") demotes that one bullet to nice_to_have rather
        # than dropping it, since the section header alone isn't decisive.
        bucket = must_have if _classify_bullet_must_or_nice(bullet) == "must" else nice_to_have
        bucket.extend(_bullet_to_requirements(bullet))

    for line in sections["nice"]:
        bullet = _clean_bullet(line)
        if not bullet:
            continue
        nice_to_have.extend(_bullet_to_requirements(bullet))

    required_years_total = _extract_required_years_total(sections)
    seniority = _infer_seniority(resolved_title, required_years_total)

    role_families = load_role_families()
    responsibility_text = " ".join(sections["responsibility"] + sections["other"])
    role_family = role_families.classify(resolved_title, responsibility_text)

    taxonomy = load_skills_taxonomy()
    domain_terms = taxonomy.find_in_text(responsibility_text)
    domain = domain_terms[0] if domain_terms else None

    return JobRequirements(
        title=resolved_title,
        seniority=seniority,
        must_have=must_have,
        nice_to_have=nice_to_have,
        required_years_total=required_years_total,
        domain=domain,
        role_family=role_family,
    )
