"""build_profile(cv_text) -> CandidateProfile: the full candidate profile,
including the two fields cv_extractor.extract_candidate_profile() doesn't
compute - the family-affinity vector and signature skills. See
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 3.

Pure: everything here takes strings/dates/dicts in and returns a
CandidateProfile, same as the rest of this package. Manual overrides are a
dict the caller supplies (loaded from wherever jobfit/data keeps them) -
this module never touches a file path.
"""

from __future__ import annotations

import re
from datetime import date

from jobfit.ats_scorer import cv_extractor
from jobfit.ats_scorer.config import DEFAULT_CONFIG, ScoringConfig
from jobfit.ats_scorer.models import CandidateProfile, Role, SkillEvidence
from jobfit.ats_scorer.taxonomy import load_role_families, load_skill_idf

_YEAR_RE = re.compile(r"\d{4}")


def _year_of(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEAR_RE.search(value)
    return int(match.group()) if match else None


def _role_weight(role: Role, now_year: int, decay_years: float) -> float:
    """duration_years * recency_decay(years_ago) - a current role at full
    weight, decaying linearly to 0 by decay_years since the role ended.
    Floors duration at 0.5 so a real but sub-year role (an internship, a
    short contract) still counts for something instead of vanishing."""
    if not role.family:
        return 0.0
    start_year = _year_of(role.start)
    if start_year is None:
        return 0.0
    end_year = now_year if role.end in (None, "present") else (_year_of(role.end) or now_year)
    duration = max(end_year - start_year, 0.5)
    years_ago = max(now_year - end_year, 0)
    decay = max(0.0, 1.0 - years_ago / decay_years) if decay_years > 0 else float(years_ago == 0)
    return duration * decay


def family_affinity(roles: list[Role], now: date, decay_years: float) -> dict[str, float]:
    """A full vector over every known family (0.0 where the candidate has
    no weighted recent experience in it), not just the top match - the
    adjacency-aware family_fit gate (section 5) needs the whole shape, not
    a single winner.

    Args:
        roles: The candidate's parsed roles, each already classified into
            a family by _parse_roles.
        now: Reference date years-ago is measured from.
        decay_years: Recency decay window - see _role_weight.

    Returns:
        {family: affinity in [0, 1]}, summing to 1.0 across every role
        that has both a family and a weight, or all zeros when no role
        does (no work history, or everything beyond the decay window).
    """
    affinity = {family: 0.0 for family in load_role_families().families}
    weights = {}
    for i, role in enumerate(roles):
        w = _role_weight(role, now.year, decay_years)
        if w > 0 and role.family:
            weights[i] = w
            affinity[role.family] = affinity.get(role.family, 0.0) + w
    total = sum(weights.values())
    if total > 0:
        affinity = {family: round(value / total, 4) for family, value in affinity.items()}
    return affinity


def signature_skills(skills: list[SkillEvidence], idf: dict[str, float], threshold: float) -> list[str]:
    """Skills backed by real role-bullet evidence (not just listed) whose
    IDF clears the signature bar - see ProfileConfig.signature_idf_threshold
    for why 6.0 and real example values."""
    return sorted({
        s.canonical for s in skills
        if s.evidence_strength == "strong" and idf.get(s.canonical, 0.0) >= threshold
    })


def build_profile(
    cv_text: str, config: ScoringConfig = DEFAULT_CONFIG, reference_date: date | None = None,
) -> CandidateProfile:
    """The full candidate profile: everything extract_candidate_profile()
    parses from CV text, plus the family-affinity vector and signature
    skill list the new scoring formula needs.

    Args:
        cv_text: The full CV body text.
        config: Scoring configuration (recency decay window, signature IDF
            threshold).
        reference_date: The date "now" is measured from. Defaults to today.

    Returns:
        A CandidateProfile with family_affinity and signature_skills filled
        in alongside the fields extract_candidate_profile() already sets.
    """
    now = reference_date or date.today()
    profile = cv_extractor.extract_candidate_profile(cv_text, reference_date=now)
    idf = load_skill_idf()
    return profile.model_copy(update={
        "family_affinity": family_affinity(profile.roles, now, config.profile.recency_decay_years),
        "signature_skills": signature_skills(profile.skills, idf, config.profile.signature_idf_threshold),
    })


def apply_family_overrides(profile: CandidateProfile, overrides: dict[str, str]) -> CandidateProfile:
    """Apply manual {family: "boost"|"block"} overrides on top of a
    computed profile - strictly the last step, per the spec. A blocked
    family's affinity is zeroed (and the remaining weight renormalized); a
    boosted family is set to the highest affinity of any family plus a
    fixed margin, then the vector is renormalized so it still sums to 1.0.

    Args:
        profile: An already-built CandidateProfile.
        overrides: {family name: "boost" or "block"}. An unknown family
            name or value is ignored rather than raising - a stale override
            file must never crash scoring.

    Returns:
        A new CandidateProfile with family_affinity adjusted.
    """
    if not overrides:
        return profile
    affinity = dict(profile.family_affinity)
    boosted = [f for f, action in overrides.items() if action == "boost" and f in affinity]
    blocked = [f for f, action in overrides.items() if action == "block" and f in affinity]

    for family in blocked:
        affinity[family] = 0.0
    if boosted:
        ceiling = max(affinity.values(), default=0.0)
        for family in boosted:
            affinity[family] = ceiling + 0.25

    total = sum(affinity.values())
    if total > 0:
        affinity = {family: round(value / total, 4) for family, value in affinity.items()}
    return profile.model_copy(update={"family_affinity": affinity})
