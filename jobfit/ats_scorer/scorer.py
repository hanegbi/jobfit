"""Pure scoring: combines a MatchResult into five weighted sub-scores, a
hard-gated final score, a fixed-meaning band, and an explainable
ScoreResult. No I/O - takes already-extracted, already-matched data in and
returns a result, so it's trivially unit testable.
"""

import re

from jobfit.ats_scorer.config import DEFAULT_CONFIG, ScoringConfig
from jobfit.ats_scorer.models import (
    CandidateProfile, JobRequirements, MatchedRequirement, MatchResult, MatchStrength,
    RequirementKind, ScoreResult, SubScore,
)

_HARD_REQUIREMENT_KINDS = frozenset({
    RequirementKind.YEARS, RequirementKind.DEGREE, RequirementKind.CERTIFICATION,
    RequirementKind.LANGUAGE, RequirementKind.LOCATION,
})

_EVIDENCE_MARKER_RE = re.compile(
    r"\b(\d+%|\$\d[\d,]*|\d+[kmb]\+?|production|owned|designed|led|architected|scaled|"
    r"launched|shipped|built from scratch|reduced|increased|improved|optimized|"
    r"delivered|drove|spearheaded)\b",
    re.IGNORECASE,
)

_STRENGTH_LABEL = {
    MatchStrength.STRONG: "strong", MatchStrength.MEDIUM: "medium",
    MatchStrength.WEAK: "weak", MatchStrength.NONE: "none",
}


def _coverage_score(matches: list[MatchedRequirement], config: ScoringConfig, empty_note: str) -> SubScore:
    """Weighted fraction of requirements matched, by match strength.

    Args:
        matches: The matched requirements to score coverage over.
        config: Scoring configuration (match-strength weights).
        empty_note: Reason text to report when there are no requirements
            to cover at all.

    Returns:
        A SubScore in [0, 100].
    """
    if not matches:
        return SubScore(score=100, weight=0.0, reasons=[empty_note])
    weights = config.match_strength_weights
    strength_weight = {
        MatchStrength.STRONG: weights.strong, MatchStrength.MEDIUM: weights.medium,
        MatchStrength.WEAK: weights.weak, MatchStrength.NONE: weights.none,
    }
    total = sum(strength_weight[m.strength] for m in matches)
    score = round(100.0 * total / len(matches))
    matched_count = sum(1 for m in matches if m.strength != MatchStrength.NONE)
    reasons = [f"{matched_count}/{len(matches)} requirements matched"]
    return SubScore(score=score, weight=0.0, reasons=reasons)


def _title_and_seniority_fit_score(match_result: MatchResult) -> SubScore:
    """Role-family and seniority-level fit, per config.GateConfig's
    seniority_gap_threshold-independent point deductions (-40 for a role
    family mismatch, -30 per level of seniority gap).

    Args:
        match_result: The match result (role_family_match, seniority_gap).

    Returns:
        A SubScore in [0, 100].
    """
    score = 100
    reasons = []
    if not match_result.role_family_match:
        score -= 40
        reasons.append("role family does not match the job's")
    else:
        reasons.append("role family matches the job's")
    gap = abs(match_result.seniority_gap)
    if gap > 0:
        score -= 30 * gap
        direction = "more senior" if match_result.seniority_gap > 0 else "less senior"
        reasons.append(f"{gap} seniority level(s) {direction} than the job")
    score = max(0, min(100, score))
    return SubScore(score=score, weight=0.0, reasons=reasons)


def _experience_relevance_score(match_result: MatchResult) -> SubScore:
    """Recent-work domain relevance, directly from
    MatchResult.recent_relevant_fraction.

    Args:
        match_result: The match result.

    Returns:
        A SubScore in [0, 100].
    """
    score = round(100.0 * match_result.recent_relevant_fraction)
    pct = round(match_result.recent_relevant_fraction * 100)
    reasons = [f"{pct}% of recent work judged relevant to this job's domain/role family"]
    return SubScore(score=score, weight=0.0, reasons=reasons)


def _evidence_depth_score(profile: CandidateProfile) -> SubScore:
    """Rewards bullets that show scale, ownership, and outcomes; penalizes
    a CV that is a bare skills list with no substantive role descriptions.

    Args:
        profile: The candidate's structured profile.

    Returns:
        A SubScore in [0, 100].
    """
    all_bullets = [b for role in profile.roles for b in role.bullets]
    if not all_bullets:
        return SubScore(score=0, weight=0.0, reasons=["no role bullets found - CV shows no substantive work descriptions"])
    with_evidence = sum(1 for b in all_bullets if _EVIDENCE_MARKER_RE.search(b))
    fraction = with_evidence / len(all_bullets)
    score = min(100, round(fraction * 150))
    reasons = [f"{with_evidence}/{len(all_bullets)} bullets show scale/ownership/outcome evidence"]
    return SubScore(score=score, weight=0.0, reasons=reasons)


def _apply_gates(
    weighted_score: int, must_have_matches: list[MatchedRequirement], match_result: MatchResult,
    profile: CandidateProfile, job: JobRequirements, config: ScoringConfig,
) -> tuple[int, list[str]]:
    """Apply hard gates to a weighted score, taking the minimum of the
    score and every triggered gate's cap.

    Args:
        weighted_score: The score before gating.
        must_have_matches: Matched must_have requirements.
        match_result: The full match result.
        profile: The candidate's structured profile.
        job: The job's structured requirements.
        config: Scoring configuration (gate thresholds/caps).

    Returns:
        A (gated_score, gate_names_applied) tuple.
    """
    gates = config.gates
    score = weighted_score
    applied = []

    unmet_hard = [
        m for m in must_have_matches
        if m.requirement.kind in _HARD_REQUIREMENT_KINDS and m.strength == MatchStrength.NONE
    ]
    if unmet_hard:
        score = min(score, gates.unmet_hard_requirement_cap)
        applied.append("unmet_hard_requirement")

    missing_skills = [
        m for m in must_have_matches
        if m.requirement.kind == RequirementKind.SKILL and m.strength == MatchStrength.NONE
    ]
    if len(missing_skills) >= gates.missing_skills_threshold:
        score = min(score, gates.missing_skills_cap)
        applied.append("missing_skills")

    if abs(match_result.seniority_gap) >= gates.seniority_gap_threshold:
        score = min(score, gates.seniority_gap_cap)
        applied.append("seniority_gap")

    recent_families = {r.family for r in profile.roles[:2] if r.family}
    if job.role_family and recent_families and job.role_family not in recent_families:
        score = min(score, gates.role_family_mismatch_cap)
        applied.append("role_family_mismatch")

    return max(0, min(100, score)), applied


def _rank_gaps(must_have_matches: list[MatchedRequirement], nice_to_have_matches: list[MatchedRequirement],
               match_result: MatchResult) -> list[str]:
    """Return up to three gap descriptions, most-impactful first.

    Args:
        must_have_matches: Matched must_have requirements.
        nice_to_have_matches: Matched nice_to_have requirements.
        match_result: The full match result.

    Returns:
        Up to three human-readable gap descriptions.
    """
    gaps = []
    for m in must_have_matches:
        if m.strength == MatchStrength.NONE:
            gaps.append(f"Missing must-have: {m.requirement.text}")
    if not match_result.role_family_match:
        gaps.append("Recent role family does not match the job's role family")
    if match_result.seniority_gap != 0:
        direction = "more senior" if match_result.seniority_gap > 0 else "less senior"
        gaps.append(f"Seniority is {abs(match_result.seniority_gap)} level(s) {direction} than the job")
    for m in must_have_matches:
        if m.strength == MatchStrength.WEAK:
            gaps.append(f"Weak evidence for must-have: {m.requirement.text}")
    for m in nice_to_have_matches:
        if m.strength == MatchStrength.NONE:
            gaps.append(f"Missing nice-to-have: {m.requirement.text}")
    return gaps[:3]


def _summary(score: int, band: str, gaps: list[str], must_have_matches: list[MatchedRequirement]) -> str:
    matched_count = sum(1 for m in must_have_matches if m.strength != MatchStrength.NONE)
    total_count = len(must_have_matches)
    first = f"This CV scores {score}/100 ({band}), covering {matched_count} of {total_count} must-have requirements."
    second = f"Biggest gap: {gaps[0]}." if gaps else "No significant gaps identified."
    return f"{first} {second}"


def score(
    profile: CandidateProfile, job: JobRequirements, match_result: MatchResult,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> ScoreResult:
    """Compute the final ScoreResult from an already-matched CV/job pair.

    Args:
        profile: The candidate's structured profile.
        job: The job's structured requirements.
        match_result: The result of matcher.match(profile, job, config).
        config: Scoring configuration.

    Returns:
        A ScoreResult with the weighted score, band, sub-scores, applied
        gates, and explanatory fields.
    """
    weights = config.weights
    must_have_sub = _coverage_score(match_result.must_have_matches, config, "no must_have requirements identified in the job description")
    title_sub = _title_and_seniority_fit_score(match_result)
    experience_sub = _experience_relevance_score(match_result)
    nice_to_have_sub = _coverage_score(match_result.nice_to_have_matches, config, "no nice_to_have requirements identified in the job description")
    evidence_sub = _evidence_depth_score(profile)

    must_have_sub.weight = weights.must_have_coverage
    title_sub.weight = weights.title_and_seniority_fit
    experience_sub.weight = weights.experience_relevance
    nice_to_have_sub.weight = weights.nice_to_have_coverage
    evidence_sub.weight = weights.evidence_depth

    raw = (
        must_have_sub.weight * must_have_sub.score
        + title_sub.weight * title_sub.score
        + experience_sub.weight * experience_sub.score
        + nice_to_have_sub.weight * nice_to_have_sub.score
        + evidence_sub.weight * evidence_sub.score
    )
    weighted_score = round(raw)

    gated_score, gates_applied = _apply_gates(
        weighted_score, match_result.must_have_matches, match_result, profile, job, config,
    )

    band = config.bands.band_for(gated_score)
    gaps = _rank_gaps(match_result.must_have_matches, match_result.nice_to_have_matches, match_result)

    matched_must_haves = [
        {"requirement": m.requirement.text, "evidence": m.evidence, "strength": _STRENGTH_LABEL[m.strength]}
        for m in match_result.must_have_matches if m.strength != MatchStrength.NONE
    ]
    missing_must_haves = [m.requirement.text for m in match_result.must_have_matches if m.strength == MatchStrength.NONE]
    matched_nice_to_haves = [
        {"requirement": m.requirement.text, "evidence": m.evidence, "strength": _STRENGTH_LABEL[m.strength]}
        for m in match_result.nice_to_have_matches if m.strength != MatchStrength.NONE
    ]

    return ScoreResult(
        score=gated_score,
        band=band,
        sub_scores={
            "must_have_coverage": must_have_sub,
            "title_and_seniority_fit": title_sub,
            "experience_relevance": experience_sub,
            "nice_to_have_coverage": nice_to_have_sub,
            "evidence_depth": evidence_sub,
        },
        gates_applied=gates_applied,
        matched_must_haves=matched_must_haves,
        missing_must_haves=missing_must_haves,
        matched_nice_to_haves=matched_nice_to_haves,
        top_gaps=gaps,
        summary=_summary(gated_score, band, gaps, match_result.must_have_matches),
    )
