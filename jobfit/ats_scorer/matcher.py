"""Matches a CandidateProfile against JobRequirements: for every
requirement, how strongly does the CV back it, plus role-family,
seniority, and recent-experience-relevance signals.
"""

from datetime import date

from jobfit.ats_scorer.config import DEFAULT_CONFIG, ScoringConfig
from jobfit.ats_scorer.cv_extractor import recent_role_family
from jobfit.ats_scorer.models import (
    CandidateProfile, JobRequirements, MatchedRequirement, MatchResult,
    MatchStrength, Requirement, RequirementKind, SENIORITY_LEVEL,
)


def _skill_match_strength(
    requirement: Requirement, profile: CandidateProfile, job: JobRequirements, config: ScoringConfig,
) -> tuple[MatchStrength, str | None]:
    """Return (strength, evidence) for a single SKILL requirement.

    Args:
        requirement: The skill requirement (requirement.canonical is set).
        profile: The candidate's structured profile.
        job: The job's structured requirements (used to check for "related
            role context" when a skill is only weakly evidenced).
        config: Scoring configuration (stale_skill_years threshold).

    Returns:
        A (MatchStrength, evidence description or None) pair.
    """
    evidence = next((s for s in profile.skills if s.canonical == requirement.canonical), None)
    if evidence is None:
        return MatchStrength.NONE, None

    if evidence.evidence_strength == "strong":
        if evidence.recency_years is not None and evidence.recency_years > config.stale_skill_years:
            return MatchStrength.MEDIUM, f"{evidence.canonical}: used {evidence.recency_years}y ago (role evidence, stale)"
        return MatchStrength.STRONG, f"{evidence.canonical}: role evidence, recent"

    # weak (skills-list only) - bump to medium if the candidate has a role
    # in the same family as the job, giving the bare keyword some context.
    family = recent_role_family(profile)
    if job.role_family and family == job.role_family:
        return MatchStrength.MEDIUM, f"{evidence.canonical}: listed in skills section, related role context"
    return MatchStrength.WEAK, f"{evidence.canonical}: listed in skills section only"


def _years_requirement_strength(requirement: Requirement, profile: CandidateProfile, now: date) -> tuple[MatchStrength, str | None]:
    required = requirement.years
    if required is None:
        return MatchStrength.NONE, None
    total_years = _total_years_experience(profile, now)
    if total_years is None:
        return MatchStrength.NONE, None
    if total_years >= required:
        return MatchStrength.STRONG, f"{total_years} years total experience (requires {required})"
    if total_years >= required - 1:
        return MatchStrength.MEDIUM, f"{total_years} years total experience (requires {required})"
    return MatchStrength.NONE, None


def _total_years_experience(profile: CandidateProfile, now: date) -> int | None:
    starts = []
    for role in profile.roles:
        if role.start:
            try:
                starts.append(int(role.start))
            except ValueError:
                continue
    if not starts:
        return None
    return now.year - min(starts)


def _list_membership_strength(requirement: Requirement, candidate_entries: list[str]) -> tuple[MatchStrength, str | None]:
    req_text = requirement.text.lower()
    for entry in candidate_entries:
        entry_lower = entry.lower()
        if entry_lower in req_text or req_text in entry_lower or _significant_word_overlap(entry_lower, req_text):
            return MatchStrength.STRONG, entry
    return MatchStrength.NONE, None


def _significant_word_overlap(a: str, b: str, min_shared: int = 2) -> bool:
    words_a = {w for w in a.split() if len(w) > 3}
    words_b = {w for w in b.split() if len(w) > 3}
    return len(words_a & words_b) >= min_shared


def _domain_requirement_strength(requirement: Requirement, profile: CandidateProfile) -> tuple[MatchStrength, str | None]:
    req_words = {w for w in requirement.text.lower().split() if len(w) > 3}
    for role in profile.roles:
        role_text = f"{role.title} {' '.join(role.bullets)}".lower()
        role_words = {w for w in role_text.split() if len(w) > 3}
        shared = req_words & role_words
        if len(shared) >= 3:
            return MatchStrength.STRONG, f"{role.title}: overlapping context ({', '.join(sorted(shared)[:3])})"
        if len(shared) >= 1:
            return MatchStrength.WEAK, f"{role.title}: partial overlap ({', '.join(sorted(shared))})"
    return MatchStrength.NONE, None


def match_requirement(
    requirement: Requirement, profile: CandidateProfile, job: JobRequirements, config: ScoringConfig, now: date,
) -> MatchedRequirement:
    """Return the MatchedRequirement for one requirement against one profile.

    Args:
        requirement: The requirement to match.
        profile: The candidate's structured profile.
        job: The job's structured requirements.
        config: Scoring configuration.
        now: Reference date for years/recency calculations.

    Returns:
        A MatchedRequirement with the requirement's best-available match
        strength and supporting evidence.
    """
    if requirement.kind == RequirementKind.SKILL:
        strength, evidence = _skill_match_strength(requirement, profile, job, config)
    elif requirement.kind == RequirementKind.YEARS:
        strength, evidence = _years_requirement_strength(requirement, profile, now)
    elif requirement.kind == RequirementKind.DEGREE:
        strength, evidence = _list_membership_strength(requirement, profile.education)
    elif requirement.kind == RequirementKind.CERTIFICATION:
        strength, evidence = _list_membership_strength(requirement, profile.certifications)
    elif requirement.kind == RequirementKind.LANGUAGE:
        strength, evidence = _list_membership_strength(requirement, profile.languages)
    elif requirement.kind == RequirementKind.LOCATION:
        entries = [profile.location] if profile.location else []
        strength, evidence = _list_membership_strength(requirement, entries)
    else:
        strength, evidence = _domain_requirement_strength(requirement, profile)

    return MatchedRequirement(requirement=requirement, strength=strength, evidence=evidence)


def _years_in_role_within_window(role, now: date, window_start_year: int) -> float:
    if not role.start:
        return 0.0
    try:
        start_year = int(role.start)
    except ValueError:
        return 0.0
    end_year = now.year if role.end == "present" else None
    if end_year is None and role.end:
        try:
            end_year = int(role.end)
        except ValueError:
            end_year = None
    if end_year is None:
        return 0.0
    clipped_start = max(start_year, window_start_year)
    clipped_end = min(end_year, now.year)
    return max(0.0, clipped_end - clipped_start)


def _recent_relevant_fraction(profile: CandidateProfile, job: JobRequirements, config: ScoringConfig, now: date) -> float:
    """Fraction of the candidate's recent (config.experience.recent_years_window)
    work years judged relevant to the job's role family/domain."""
    window_start_year = now.year - config.experience.recent_years_window
    total = 0.0
    relevant = 0.0
    for role in profile.roles:
        years = _years_in_role_within_window(role, now, window_start_year)
        if years <= 0:
            continue
        total += years
        role_text = f"{role.title} {' '.join(role.bullets)}".lower()
        if job.role_family and role.family == job.role_family:
            relevant += years * 1.0
        elif job.domain and job.domain.lower() in role_text:
            relevant += years * config.experience.adjacent_domain_credit
    return relevant / total if total > 0 else 0.0


def match(
    profile: CandidateProfile, job: JobRequirements, config: ScoringConfig = DEFAULT_CONFIG, now: date | None = None,
) -> MatchResult:
    """Match a candidate against a job's requirements.

    Args:
        profile: The candidate's structured profile.
        job: The job's structured requirements.
        config: Scoring configuration.
        now: Reference date for years/recency calculations. Defaults to today.

    Returns:
        A MatchResult covering every must_have/nice_to_have requirement
        plus role-family, seniority, and recent-relevance signals.
    """
    now = now or date.today()
    must_have_matches = [match_requirement(r, profile, job, config, now) for r in job.must_have]
    nice_to_have_matches = [match_requirement(r, profile, job, config, now) for r in job.nice_to_have]

    family = recent_role_family(profile)
    role_family_match = bool(job.role_family) and family == job.role_family

    seniority_gap = SENIORITY_LEVEL[profile.seniority] - SENIORITY_LEVEL[job.seniority]
    recent_relevant_fraction = _recent_relevant_fraction(profile, job, config, now)

    return MatchResult(
        must_have_matches=must_have_matches,
        nice_to_have_matches=nice_to_have_matches,
        role_family_match=role_family_match,
        seniority_gap=seniority_gap,
        recent_relevant_fraction=recent_relevant_fraction,
    )
