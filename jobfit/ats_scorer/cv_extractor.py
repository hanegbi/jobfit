"""Rules-based extraction of a structured CandidateProfile from raw CV text.
No LLM calls. PDF/DOCX parsing is a thin text-extraction layer only - all
structure comes from regex/keyword rules applied to the resulting text.
"""

import re
from datetime import date
from pathlib import Path

from jobfit.ats_scorer.models import CandidateProfile, Role, Seniority, SkillEvidence
from jobfit.ats_scorer.patterns import CERTIFICATION_RE, DATE_RANGE_RE, DEGREE_RE, LANGUAGE_RE, LOCATION_RE
from jobfit.ats_scorer.taxonomy import load_role_families, load_skills_taxonomy

_SENIORITY_TITLE_PATTERNS: list[tuple[Seniority, re.Pattern]] = [
    (Seniority.HEAD, re.compile(r"\bhead of\b", re.IGNORECASE)),
    (Seniority.MANAGER, re.compile(r"\b(manager|vp|vice president|cto)\b", re.IGNORECASE)),
    (Seniority.PRINCIPAL, re.compile(r"\bprincipal\b", re.IGNORECASE)),
    (Seniority.STAFF, re.compile(r"\bstaff\b", re.IGNORECASE)),
    (Seniority.LEAD, re.compile(r"\b(lead|tech lead|team lead)\b", re.IGNORECASE)),
    (Seniority.SENIOR, re.compile(r"\bsenior\b|\bsr\.?\b", re.IGNORECASE)),
    (Seniority.JUNIOR, re.compile(r"\bjunior\b|\bjr\.?\b|\bentry.level\b|\bintern\b", re.IGNORECASE)),
]

_SKILLS_HEADER_WORDS = ("skills", "technical skills", "technologies", "tech stack", "core competencies")
_ALL_SECTION_HEADER_WORDS = _SKILLS_HEADER_WORDS + (
    "education", "experience", "work experience", "employment",
    "certifications", "languages", "summary", "about",
)
# Matches either a header alone on its own line ("Skills") or a header with
# inline content after a colon ("Skills: Python, Go, Rust") - both are
# common resume formats and must be handled the same way everywhere a
# section boundary matters.
_SKILLS_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:" + "|".join(_SKILLS_HEADER_WORDS) + r")\s*:?\s*(.*)$", re.IGNORECASE,
)
_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:" + "|".join(_ALL_SECTION_HEADER_WORDS) + r")\s*:?\s*(.*)$", re.IGNORECASE,
)

_CITY_LOCATION_RE = re.compile(
    r"\b(tel aviv|jerusalem|haifa|herzliya|ramat gan|petah tikva|netanya|beer sheva|"
    r"new york|san francisco|london|berlin|austin|seattle|boston|remote)\b",
    re.IGNORECASE,
)


def _split_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in (text or "").splitlines()]


def extract_text(path) -> str:
    """Extract raw text from a .docx, .pdf, or plain-text CV file.

    Args:
        path: Path to the CV file.

    Returns:
        The file's extracted text.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pypdf
        reader = pypdf.PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(p for p in parts if p.strip())
    if suffix == ".docx":
        import docx
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)
        return "\n".join(parts)
    return path.read_text(encoding="utf-8")


def _normalize_year(token: str) -> int | None:
    match = re.search(r"\d{4}", token)
    return int(match.group()) if match else None


def _find_date_ranges(lines: list[str]) -> list[tuple[int, str, str]]:
    """Return (line_index, start, end) for every line containing a date range."""
    found = []
    for i, line in enumerate(lines):
        match = DATE_RANGE_RE.search(line)
        if match:
            found.append((i, match.group(1).strip(), match.group(2).strip()))
    return found


def _looks_like_bullet_or_blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith(("-", "*", "•", "●", "‣", "⁃"))


def _infer_title_for_role(lines: list[str], date_line_idx: int, remainder: str) -> tuple[str, str | None, int]:
    """Infer (title, company, anchor_idx) for the role anchored at date_line_idx.

    Args:
        lines: All CV lines.
        date_line_idx: Index of the line containing this role's date range.
        remainder: The date line's text with the date range itself removed.

    Returns:
        A (title, company, anchor_idx) tuple. company may be None when it
        can't be separated from the title with confidence. anchor_idx is
        the earliest line index that belongs to this role (its own title
        line, when found on a separate line, else the date line itself) -
        the boundary the previous role's bullet collection must stop before.
    """
    remainder = remainder.strip(" -|,:–—")
    if remainder:
        parts = re.split(r"\s*[|,]\s*", remainder)
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip(), date_line_idx
        prev_line = lines[date_line_idx - 1].strip() if date_line_idx > 0 else ""
        if prev_line and not _looks_like_bullet_or_blank(prev_line) and not DATE_RANGE_RE.search(prev_line):
            return prev_line, remainder, date_line_idx - 1
        return remainder, None, date_line_idx

    prev_line = lines[date_line_idx - 1].strip() if date_line_idx > 0 else ""
    if prev_line and not _looks_like_bullet_or_blank(prev_line) and not DATE_RANGE_RE.search(prev_line):
        return prev_line, None, date_line_idx - 1
    return "", None, date_line_idx


def _parse_roles(lines: list[str]) -> list[Role]:
    """Parse employment roles from CV lines using date-range anchors, per
    the format: a title line, then a company+date-range line (or a
    combined "Title | Company | Dates" line), then bullets until the next
    role's title/date-range line."""
    date_lines = _find_date_ranges(lines)
    parsed = []
    for line_idx, start_raw, end_raw in date_lines:
        line = lines[line_idx]
        match = DATE_RANGE_RE.search(line)
        remainder = line[: match.start()] + line[match.end():]
        title, company, anchor_idx = _infer_title_for_role(lines, line_idx, remainder)
        if not title:
            continue
        start = str(_normalize_year(start_raw)) if _normalize_year(start_raw) else None
        end = "present" if re.search(r"present|current|now|ongoing", end_raw, re.IGNORECASE) else (
            str(_normalize_year(end_raw)) if _normalize_year(end_raw) else None
        )
        parsed.append({"line_idx": line_idx, "anchor_idx": anchor_idx, "title": title, "company": company, "start": start, "end": end})

    role_families = load_role_families()
    roles: list[Role] = []
    for idx, entry in enumerate(parsed):
        next_anchor_idx = parsed[idx + 1]["anchor_idx"] if idx + 1 < len(parsed) else len(lines)
        bullet_start = entry["line_idx"] + 1
        bullets = []
        for ln in lines[bullet_start:next_anchor_idx]:
            stripped = ln.strip()
            if not stripped or _SECTION_HEADER_RE.match(stripped) or DATE_RANGE_RE.search(stripped):
                continue
            cleaned = re.sub(r"^\s*[-*•●‣⁃]\s*", "", stripped).strip()
            if cleaned and cleaned != entry["company"]:
                bullets.append(cleaned)

        family = role_families.classify(entry["title"], " ".join(bullets))
        roles.append(Role(
            title=entry["title"], company=entry["company"], start=entry["start"], end=entry["end"],
            bullets=bullets, family=family,
        ))
    return roles


def _extract_skills_section_text(lines: list[str]) -> str:
    """Return the text under a "Skills"/"Technologies" header - either
    inline content on the header's own line ("Skills: Python, Go") or a
    block of following lines up to the next recognized section header
    ("Skills" alone, then a list below it)."""
    blocks = []
    for i, line in enumerate(lines):
        match = _SKILLS_SECTION_HEADER_RE.match(line.strip())
        if not match:
            continue
        inline_content = match.group(1).strip()
        if inline_content:
            blocks.append(inline_content)
            continue
        block = []
        for ln in lines[i + 1:]:
            if _SECTION_HEADER_RE.match(ln.strip()):
                break
            block.append(ln)
        blocks.append("\n".join(block))
    return "\n".join(blocks)


def _recency_years(end: str | None, now: date) -> int | None:
    if end is None:
        return None
    if end == "present":
        return 0
    year = _normalize_year(end)
    return max(0, now.year - year) if year else None


def _extract_skills(roles: list[Role], skills_section_text: str, now: date) -> list[SkillEvidence]:
    """Merge skill evidence from role bullets (strong) and the skills
    section (weak), preferring the strongest/most-recent evidence per
    canonical skill when a skill appears in multiple places."""
    taxonomy = load_skills_taxonomy()
    best: dict[str, SkillEvidence] = {}

    for role in roles:
        bullet_text = " ".join(role.bullets)
        for canonical in taxonomy.find_in_text(f"{role.title} {bullet_text}"):
            years = None
            start_year = _normalize_year(role.start) if role.start else None
            end_year = None if role.end == "present" else (_normalize_year(role.end) if role.end else None)
            if start_year and (end_year or role.end == "present"):
                years = (now.year if role.end == "present" else end_year) - start_year
            recency = _recency_years(role.end, now)
            existing = best.get(canonical)
            if existing is None or (existing.recency_years is not None and recency is not None and recency < existing.recency_years):
                best[canonical] = SkillEvidence(
                    canonical=canonical, evidence_strength="strong",
                    years=years, recency_years=recency,
                )

    for canonical in taxonomy.find_in_text(skills_section_text):
        if canonical not in best:
            best[canonical] = SkillEvidence(canonical=canonical, evidence_strength="weak", years=None, recency_years=None)

    return list(best.values())


def _infer_seniority(roles: list[Role], now: date) -> Seniority:
    for role in roles[:1]:
        for level, pattern in _SENIORITY_TITLE_PATTERNS:
            if pattern.search(role.title):
                return level
    total_years = _total_years_experience(roles, now)
    if total_years is None:
        return Seniority.MID
    if total_years <= 2:
        return Seniority.JUNIOR
    if total_years <= 5:
        return Seniority.MID
    return Seniority.SENIOR


def _total_years_experience(roles: list[Role], now: date) -> int | None:
    start_years = [_normalize_year(r.start) for r in roles if r.start]
    start_years = [y for y in start_years if y]
    if not start_years:
        return None
    return now.year - min(start_years)


def extract_candidate_profile(text: str, reference_date: date | None = None) -> CandidateProfile:
    """Extract a structured CandidateProfile from raw CV text.

    Args:
        text: The full CV body text.
        reference_date: The date "now" is measured from, for recency and
            total-years calculations. Defaults to today.

    Returns:
        A CandidateProfile built entirely from patterns found in the text.
    """
    now = reference_date or date.today()
    lines = _split_lines(text or "")

    roles = _parse_roles(lines)
    roles.sort(key=lambda r: _normalize_year(r.start) or 0, reverse=True)

    skills_section_text = _extract_skills_section_text(lines)
    skills = _extract_skills(roles, skills_section_text, now)

    role_families = load_role_families()
    domains = sorted({r.family for r in roles[:2] if r.family})

    full_text = text or ""
    education = [m.group().strip() for m in DEGREE_RE.finditer(full_text)]
    certifications = [m.group().strip() for m in CERTIFICATION_RE.finditer(full_text)]
    languages = [m.group().strip() for m in LANGUAGE_RE.finditer(full_text)]
    location_match = LOCATION_RE.search(full_text) or _CITY_LOCATION_RE.search(full_text)
    location = location_match.group().strip() if location_match else None

    seniority = _infer_seniority(roles, now)

    return CandidateProfile(
        roles=roles, skills=skills, domains=domains,
        education=education, certifications=certifications, languages=languages,
        location=location, seniority=seniority,
    )


def recent_role_family(profile: CandidateProfile) -> str | None:
    """Return the role family of the candidate's most recent role, falling
    back to the second-most-recent role's family if the most recent role
    has none classified.

    Args:
        profile: The candidate's structured profile (roles sorted newest
            first, as extract_candidate_profile produces).

    Returns:
        The recent role family, or None if no recent role has one.
    """
    for role in profile.roles[:2]:
        if role.family:
            return role.family
    return None
