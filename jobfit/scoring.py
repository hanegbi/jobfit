"""Deterministic scoring of a job against a CV-derived profile.

Ported from linkedin-match's backend/core/matching.py, trimmed to operate on
plain job dicts and a single must_have_keywords list per CV (no nice-to-have
list — the CV's own extracted skills already cover that ground) plus a
shared exclude/title-relevance/location config (jobfit.config).
"""

import re

from jobfit import config

ROLE_WEIGHT = 50
SENIORITY_PENALTY = 30
EXCLUDE_PENALTY = 15
OFF_TARGET_TITLE_PENALTY = 50
MAX_PENALTY = 90

# Weight of each signal in the final 0-100 score. FULL applies when the job
# text yields an extractable requirements list (a real description) - there,
# what the job actually asks for (coverage) dominates over its title. When
# there's no description to check (title-only techmap fallback), title/role
# match is all there is - but that's a guess, not evidence, so TITLE_ONLY_SCALE
# caps how high a title alone can score, keeping title-only jobs from
# outranking real, description-verified matches.
FULL_WEIGHTS = {"coverage": 0.70, "role": 0.20, "experience": 0.10}
TITLE_ONLY_WEIGHTS = {"role": 0.75, "experience": 0.25}
TITLE_ONLY_SCALE = 0.6

# A coverage ratio needs a real denominator to mean anything - a title alone
# ("LLM Inference Engineer") can yield 1-2 vocab hits and swing 0%/50%/100%
# on noise. Require this many requirement terms, extracted from the
# description body alone (not the title), before trusting coverage at all.
MIN_REQUIREMENTS_FOR_COVERAGE = 3

_YEARS_PATTERN = re.compile(
    r"(\d{1,2})\s*\+\s*years|(?:at least|minimum(?: of)?|over)\s*(\d{1,2})\s*years|(\d{1,2})\s*-\s*\d{1,2}\s*years",
    re.IGNORECASE,
)


def _word_match(term: str, text: str) -> bool:
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def title_is_relevant(title: str | None) -> bool:
    if not title:
        return False
    if any(_word_match(t, title) for t in config.TITLE_EXCLUDE_KEYWORDS):
        return False
    return any(_word_match(t, title) for t in config.TITLE_INCLUDE_KEYWORDS)


def is_relevant_location(location: str | None) -> bool:
    """Israel-based or remote; empty locations are kept (assume unspecified = ok)."""
    if not location:
        return True
    if any(_word_match(t, location) for t in config.REMOTE_TERMS):
        return True
    return any(_word_match(t, location) for t in config.ISRAEL_LOCATION_TERMS)


def is_remote_location(location: str | None) -> bool:
    if not location:
        return False
    return any(_word_match(t, location) for t in config.REMOTE_TERMS)


def canonical_city(location: str | None) -> str | None:
    if not location:
        return None
    for city, aliases in config.CITY_ALIASES.items():
        if any(_word_match(a, location) for a in aliases):
            return city.title()
    return None


_HEBREW_RE = re.compile(r"[֐-׿]")


def to_english_location(location: str | None) -> str | None:
    """Return an English-only version of a location string.

    Prefers a recognized Israeli city (from CITY_ALIASES); falls back to the
    raw string when it's already Latin-script; otherwise (unmapped Hebrew)
    falls back to the generic "Israel" label rather than showing Hebrew text.
    """
    if not location:
        return None
    if is_remote_location(location):
        return "Remote"
    city = canonical_city(location)
    if city:
        return city
    if _HEBREW_RE.search(location):
        return "Israel"
    return location.strip()


def required_years(text: str) -> int | None:
    found = []
    for match in _YEARS_PATTERN.finditer(text):
        value = next((g for g in match.groups() if g), None)
        if value and 1 <= int(value) <= 25:
            found.append(int(value))
    return max(found) if found else None


def _experience_pct(text: str) -> tuple[float, str | None]:
    """Return (0-100 fit score, note) from the years of experience the job states.

    No stated requirement -> neutral (50): neither rewarded nor punished.
    """
    years = required_years(text)
    if years is None:
        return 50.0, None
    gap = years - config.USER_YEARS_EXPERIENCE
    if gap <= 0:
        return 100.0, f"exp:{years}y(fit)"
    if gap == 1:
        return 70.0, f"exp:{years}y"
    return max(0.0, 100.0 - gap * 25), f"exp:{years}y(-{min(100, gap * 25):.0f})"


def _role_match(title: str, role_weights: dict[str, int]) -> tuple[float, str | None, set[str]]:
    """Return (0-100 role-fit score, matched role name, words the role covers).

    Gated on title_is_relevant: a title like "Senior iOS Software Engineer"
    contains the substring "software engineer" but is a different specialization
    entirely - matching that substring blindly gave iOS/Android/frontend/game
    roles full role credit. No credit when an exclude term is present.
    """
    if not title_is_relevant(title):
        return 0.0, None, set()
    best_weight = 0
    best_role = None
    role_words: set[str] = set()
    for role in config.TARGET_ROLES:
        if _word_match(role, title):
            weight = role_weights.get(role, ROLE_WEIGHT)
            if weight > best_weight:
                best_weight = weight
                best_role = role
            role_words.update(role.split())
    max_weight = max(role_weights.values())
    return (100.0 * best_weight / max_weight if max_weight else 0.0), best_role, role_words


def extract_job_requirements(haystack: str) -> list[str]:
    """Pull the known-skill vocabulary out of a job's own text - its implied requirements."""
    return list(dict.fromkeys(s for s in config.SKILLS_VOCAB if _word_match(s, haystack)))


def _penalty(title: str, employment_type: str | None, haystack: str) -> tuple[int, list[str]]:
    penalty = 0
    notes: list[str] = []
    if not title_is_relevant(title):
        penalty += OFF_TARGET_TITLE_PENALTY
        notes.append(f"off-target title(-{OFF_TARGET_TITLE_PENALTY})")
    seniority_text = f"{title} {employment_type or ''}"
    if any(_word_match(t, seniority_text) for t in config.OVERQUALIFIED_TITLE_TERMS):
        penalty += SENIORITY_PENALTY
        notes.append(f"senior(-{SENIORITY_PENALTY})")
    for term in config.EXCLUDE_KEYWORDS:
        if _word_match(term, haystack):
            penalty += EXCLUDE_PENALTY
            notes.append(f"-{term}")
    return min(penalty, MAX_PENALTY), notes


def score_job(job: dict, must_have_keywords: list[str], role_weights: dict[str, int] | None = None) -> dict:
    """Score one job 0-100 against one CV: % of the job's own stated skills you cover.

    job needs: title, description, department, location, employment_type.
    Returns {score, confidence, matched, requirements, coverage}:
      - confidence "full" when the job text yielded a requirements list to check
        coverage against, "title_only" when it didn't (nothing but a title/level
        to go on, so only title/role-match and experience contribute).
      - matched: the job's own requirement terms your CV covers (for chips/UI).
      - requirements: the full requirement list extracted from the job text.
    """
    role_weights = role_weights or config.ROLE_WEIGHTS_DEFAULT
    title = job.get("title") or ""
    description = job.get("description") or ""
    haystack = f"{title}\n{description}\n{job.get('department') or ''}"

    role_pct, matched_role, role_words = _role_match(title, role_weights)
    exp_pct, exp_note = _experience_pct(haystack)
    penalty, penalty_notes = _penalty(title, job.get("employment_type"), haystack)

    requirements = extract_job_requirements(description)
    cv_skills = set(must_have_keywords)

    def _is_role_word(term: str) -> bool:
        return all(word in role_words for word in term.split())

    real_requirements = [r for r in requirements if not _is_role_word(r)]
    matched = [r for r in real_requirements if r in cv_skills]

    if len(real_requirements) >= MIN_REQUIREMENTS_FOR_COVERAGE:
        coverage_pct = 100.0 * len(matched) / len(real_requirements)
        raw = (
            FULL_WEIGHTS["coverage"] * coverage_pct
            + FULL_WEIGHTS["role"] * role_pct
            + FULL_WEIGHTS["experience"] * exp_pct
        )
        confidence = "full"
    else:
        coverage_pct = None
        raw = (TITLE_ONLY_WEIGHTS["role"] * role_pct + TITLE_ONLY_WEIGHTS["experience"] * exp_pct) * TITLE_ONLY_SCALE
        confidence = "title_only"

    score = max(0, min(100, round(raw - penalty)))
    notes = [n for n in ([matched_role] if matched_role else []) + ([exp_note] if exp_note else []) + penalty_notes if n]

    return {
        "score": score,
        "confidence": confidence,
        "matched": matched,
        "requirements": real_requirements,
        "coverage_pct": round(coverage_pct) if coverage_pct is not None else None,
        "notes": notes,
    }


def score_job_both(job: dict, profiles: dict[str, dict]) -> dict:
    """Return score/coverage fields for both CV profiles plus a best-of pick."""
    result = {}
    for name, profile in profiles.items():
        role_weights = config.ROLE_WEIGHTS_BY_PROFILE.get(name, config.ROLE_WEIGHTS_DEFAULT)
        outcome = score_job(job, profile["must_have_keywords"], role_weights)
        result[f"score_{name}"] = outcome["score"]
        result[f"matched_{name}"] = outcome["matched"] + outcome["notes"]
        result[f"coverage_{name}"] = outcome["coverage_pct"]
        result[f"confidence_{name}"] = outcome["confidence"]
        result[f"requirements_{name}"] = outcome["requirements"]
    best_name = max(profiles, key=lambda n: result[f"score_{n}"])
    result["best_cv"] = best_name
    result["best_score"] = result[f"score_{best_name}"]
    result["best_confidence"] = result[f"confidence_{best_name}"]
    return result
