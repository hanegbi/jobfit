"""Pure scoring: score = family_fit x job_fit. job_fit is four weighted
sub-scores (IDF-weighted requirement coverage, seniority fit, evidence
depth) plus a signature bonus and a negative-evidence penalty, hard-gated;
family_fit is a separate multiplier, gated on its own threshold, applied
after the multiply. See
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 5.
No I/O - takes already-extracted, already-matched data in and returns a
result, so it's trivially unit testable.
"""

import re

from jobfit.ats_scorer.config import DEFAULT_CONFIG, ScoringConfig
from jobfit.ats_scorer.family_fit import family_fit as compute_family_fit
from jobfit.ats_scorer.models import (
    CandidateProfile, JobRequirements, MatchedRequirement, MatchResult, MatchStrength,
    RequirementKind, Requirement, ScoreResult, SubScore,
)
from jobfit.ats_scorer.taxonomy import load_family_adjacency, load_role_families, load_skill_idf

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


def _requirement_weight(requirement: Requirement, idf: dict[str, float]) -> float:
    """A matched requirement's weight in coverage: its skill's IDF when it
    names one (a shared "Model Quantization" or "Triton" counts for far
    more than a shared "Python"), 1.0 for a non-skill requirement or a
    skill absent from skill_idf.json (e.g. the command has never been run)."""
    if requirement.kind == RequirementKind.SKILL and requirement.canonical:
        return idf.get(requirement.canonical, 1.0)
    return 1.0


def _coverage_score(
    matches: list[MatchedRequirement], idf: dict[str, float], config: ScoringConfig, empty_note: str,
) -> SubScore:
    """IDF-weighted fraction of requirements matched, by match strength.

    Args:
        matches: The matched requirements to score coverage over.
        idf: {canonical skill: idf}, from skill_idf.json.
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
    req_weights = [_requirement_weight(m.requirement, idf) for m in matches]
    total_weight = sum(req_weights)
    weighted_hit = sum(strength_weight[m.strength] * w for m, w in zip(matches, req_weights))
    score = round(100.0 * weighted_hit / total_weight) if total_weight > 0 else 0
    matched_count = sum(1 for m in matches if m.strength != MatchStrength.NONE)
    reasons = [f"{matched_count}/{len(matches)} requirements matched (IDF-weighted)"]
    return SubScore(score=score, weight=0.0, reasons=reasons)


def _seniority_fit_score(match_result: MatchResult) -> SubScore:
    """Seniority-level fit: -30 points per level of gap, either direction.

    Role-family fit used to live here too (a flat -40), but family_fit
    (see ats_scorer/family_fit.py) replaced it with a continuous,
    adjacency-aware multiplier on the whole score - keeping both would
    double-count the same signal.

    Args:
        match_result: The match result (seniority_gap).

    Returns:
        A SubScore in [0, 100].
    """
    gap = abs(match_result.seniority_gap)
    score = max(0, 100 - 30 * gap)
    if gap == 0:
        reasons = ["seniority matches the job's level"]
    else:
        direction = "more senior" if match_result.seniority_gap > 0 else "less senior"
        reasons = [f"{gap} seniority level(s) {direction} than the job"]
    return SubScore(score=score, weight=0.0, reasons=reasons)


def _signature_bonus(job: JobRequirements, profile: CandidateProfile, config: ScoringConfig) -> tuple[float, str | None]:
    """+N on job_fit when 2+ of the job's own requirements are also the
    candidate's signature skills (ats_scorer/profile.py) - a distinct
    "this is specifically you" signal, not folded into coverage.

    Args:
        job: The job's structured requirements.
        profile: The candidate's structured profile (signature_skills).
        config: Scoring configuration (family_fit.signature_bonus).

    Returns:
        (bonus, reason) - bonus is 0.0 and reason is None below 2 matches.
    """
    job_skills = {r.canonical for r in job.must_have + job.nice_to_have if r.canonical}
    overlap = sorted(job_skills & set(profile.signature_skills))
    if len(overlap) < 2:
        return 0.0, None
    return config.family_fit.signature_bonus, f"signature skills matched: {', '.join(overlap)}"


def _negative_evidence_penalty(
    must_have_matches: list[MatchedRequirement], job: JobRequirements,
    adjacency: dict[str, dict[str, float]], config: ScoringConfig,
) -> tuple[float, list[str]]:
    """Subtracts from job_fit for a matched must-have whose own text reads
    as a different, non-adjacent family from the job's - e.g. "A/B testing"
    and "statistics" must-haves on a job classified backend. Reuses
    role_families.classify() on the requirement's own text rather than a
    second skill-to-family table; most individual requirement clauses
    won't match any family's title-shaped keywords at all, which is the
    conservative, intended behavior (never guess) rather than a bug.

    Args:
        must_have_matches: Matched must_have requirements.
        job: The job's structured requirements (role_family).
        adjacency: {family_a: {family_b: affinity}}.
        config: Scoring configuration (negative_evidence_penalty,
            adjacency_threshold, match_strength_weights).

    Returns:
        (total_penalty, reasons).
    """
    if not job.role_family:
        return 0.0, []
    role_families = load_role_families()
    weights = config.match_strength_weights
    strength_weight = {
        MatchStrength.STRONG: weights.strong, MatchStrength.MEDIUM: weights.medium,
        MatchStrength.WEAK: weights.weak, MatchStrength.NONE: weights.none,
    }
    penalty = 0.0
    reasons = []
    for m in must_have_matches:
        if m.strength == MatchStrength.NONE:
            continue
        req_family = role_families.classify(m.requirement.text)
        if not req_family or req_family == job.role_family:
            continue
        affinity = adjacency.get(req_family, {}).get(job.role_family, 0.0)
        if affinity >= config.family_fit.adjacency_threshold:
            continue
        penalty += config.family_fit.negative_evidence_penalty * strength_weight[m.strength]
        reasons.append(f'"{m.requirement.text}" reads as {req_family}, not {job.role_family}')
    return penalty, reasons


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
    config: ScoringConfig,
) -> tuple[int, list[str]]:
    """Apply job_fit's own hard gates, taking the minimum of the score and
    every triggered gate's cap. Runs on job_fit alone, before the
    family_fit multiply - family_fit's own gate (family_fit_low) is
    separate, applied by score() after the multiply, since it caps the
    final score rather than job_fit.

    Args:
        weighted_score: job_fit before gating.
        must_have_matches: Matched must_have requirements.
        match_result: The full match result.
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
    idf = load_skill_idf()
    weights = config.weights
    must_have_sub = _coverage_score(match_result.must_have_matches, idf, config, "no must_have requirements identified in the job description")
    seniority_sub = _seniority_fit_score(match_result)
    nice_to_have_sub = _coverage_score(match_result.nice_to_have_matches, idf, config, "no nice_to_have requirements identified in the job description")
    evidence_sub = _evidence_depth_score(profile)

    must_have_sub.weight = weights.must_have_coverage
    seniority_sub.weight = weights.seniority_fit
    nice_to_have_sub.weight = weights.nice_to_have_coverage
    evidence_sub.weight = weights.evidence_depth

    raw = (
        must_have_sub.weight * must_have_sub.score
        + seniority_sub.weight * seniority_sub.score
        + nice_to_have_sub.weight * nice_to_have_sub.score
        + evidence_sub.weight * evidence_sub.score
    )

    adjustments = []
    bonus, bonus_reason = _signature_bonus(job, profile, config)
    if bonus_reason:
        adjustments.append(f"+{bonus:g}: {bonus_reason}")

    adjacency = load_family_adjacency()
    penalty, penalty_reasons = _negative_evidence_penalty(match_result.must_have_matches, job, adjacency, config)
    adjustments.extend(f"-{config.family_fit.negative_evidence_penalty:g}: {reason}" for reason in penalty_reasons)

    job_fit_score = max(0, min(100, round(raw + bonus - penalty)))
    gated_job_fit, gates_applied = _apply_gates(job_fit_score, match_result.must_have_matches, match_result, config)

    fit = compute_family_fit(profile.family_affinity, job.role_family, adjacency)
    final_score = round(fit * gated_job_fit)
    if fit < config.family_fit.gate_threshold:
        final_score = min(final_score, config.family_fit.gate_cap)
        gates_applied.append("family_fit_low")
    final_score = max(0, min(100, final_score))

    band = config.bands.band_for(final_score)
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
        score=final_score,
        band=band,
        family_fit=round(fit, 4),
        sub_scores={
            "must_have_coverage": must_have_sub,
            "seniority_fit": seniority_sub,
            "nice_to_have_coverage": nice_to_have_sub,
            "evidence_depth": evidence_sub,
        },
        gates_applied=gates_applied,
        matched_must_haves=matched_must_haves,
        missing_must_haves=missing_must_haves,
        matched_nice_to_haves=matched_nice_to_haves,
        score_adjustments=adjustments,
        top_gaps=gaps,
        summary=_summary(final_score, band, gaps, match_result.must_have_matches),
    )
