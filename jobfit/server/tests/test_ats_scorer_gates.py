"""Dedicated hard-gate tests: gates exist so a CV cannot reach a "strong
match" band by piling up nice-to-haves while missing the basics, and each
gate's cap is exercised directly against the scorer module (not just
indirectly through the golden set)."""

import datetime

from jobfit.ats_scorer.config import DEFAULT_CONFIG
from jobfit.ats_scorer.pipeline import score_cv_against_job
from jobfit.ats_scorer.scorer import _apply_gates
from jobfit.ats_scorer.models import (
    CandidateProfile, JobRequirements, MatchedRequirement, MatchResult,
    MatchStrength, Requirement, RequirementKind, Role, Seniority,
)

_NOW = datetime.date(2026, 1, 1)


def test_all_nice_to_haves_and_zero_must_haves_cannot_exceed_55():
    """The headline gate spec calls out explicitly: a CV that only covers
    nice-to-haves, with no must_have coverage at all, must never reach
    even a "good match" score just by piling up bonus points."""
    cv = """Casey Rivera
Tel Aviv, Israel

Backend Engineer
Acme Corp | 2021 - Present
- Built REST APIs in Python and used PostgreSQL and GraphQL extensively in production
- Owned deployment pipelines and led a small team
"""
    jd = """Requirements:
- Required: Kubernetes experience
- Must have AWS experience
- Required: Terraform experience

Nice to have:
- PostgreSQL is a plus
- GraphQL experience is a bonus
"""
    result = score_cv_against_job(cv, jd, job_title="Backend Engineer", now=_NOW)
    assert result.score <= 55
    assert "missing_skills" in result.gates_applied


def test_apply_gates_unmet_hard_requirement_caps_regardless_of_weighted_score():
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.DEGREE, text="PhD required"),
            strength=MatchStrength.NONE,
        ),
    ]
    match_result = MatchResult(must_have_matches=must_have_matches, role_family_match=True, seniority_gap=0)
    profile = CandidateProfile(roles=[Role(title="Backend Engineer", family="backend")], seniority=Seniority.MID)
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")

    score, gates = _apply_gates(95, must_have_matches, match_result, profile, job, DEFAULT_CONFIG)

    assert score <= DEFAULT_CONFIG.gates.unmet_hard_requirement_cap
    assert "unmet_hard_requirement" in gates


def test_apply_gates_missing_skills_requires_at_least_two_none_matches():
    one_missing = [
        MatchedRequirement(requirement=Requirement(kind=RequirementKind.SKILL, text="Go", canonical="Go"), strength=MatchStrength.NONE),
        MatchedRequirement(requirement=Requirement(kind=RequirementKind.SKILL, text="Python", canonical="Python"), strength=MatchStrength.STRONG),
    ]
    match_result = MatchResult(must_have_matches=one_missing, role_family_match=True, seniority_gap=0)
    profile = CandidateProfile(roles=[Role(title="Backend Engineer", family="backend")], seniority=Seniority.MID)
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")

    score, gates = _apply_gates(90, one_missing, match_result, profile, job, DEFAULT_CONFIG)
    assert "missing_skills" not in gates

    two_missing = one_missing + [
        MatchedRequirement(requirement=Requirement(kind=RequirementKind.SKILL, text="Rust", canonical="Rust"), strength=MatchStrength.NONE),
    ]
    match_result2 = MatchResult(must_have_matches=two_missing, role_family_match=True, seniority_gap=0)
    score2, gates2 = _apply_gates(90, two_missing, match_result2, profile, job, DEFAULT_CONFIG)
    assert "missing_skills" in gates2
    assert score2 <= DEFAULT_CONFIG.gates.missing_skills_cap


def test_apply_gates_seniority_gap_requires_at_least_two_levels():
    profile = CandidateProfile(roles=[Role(title="Senior Engineer", family="backend")], seniority=Seniority.SENIOR)
    job_one_level = JobRequirements(title="Staff Engineer", seniority=Seniority.STAFF, role_family="backend")
    match_result = MatchResult(must_have_matches=[], role_family_match=True, seniority_gap=-1)
    score, gates = _apply_gates(90, [], match_result, profile, job_one_level, DEFAULT_CONFIG)
    assert "seniority_gap" not in gates

    match_result2 = MatchResult(must_have_matches=[], role_family_match=True, seniority_gap=-2)
    job_two_levels = JobRequirements(title="Principal Engineer", seniority=Seniority.PRINCIPAL, role_family="backend")
    score2, gates2 = _apply_gates(90, [], match_result2, profile, job_two_levels, DEFAULT_CONFIG)
    assert "seniority_gap" in gates2
    assert score2 <= DEFAULT_CONFIG.gates.seniority_gap_cap


def test_apply_gates_role_family_mismatch_only_when_no_recent_role_shares_the_family():
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")
    match_result = MatchResult(must_have_matches=[], role_family_match=False, seniority_gap=0)

    mismatched_profile = CandidateProfile(
        roles=[Role(title="Marketing Manager", family="marketing"), Role(title="Marketing Coordinator", family="marketing")],
        seniority=Seniority.MID,
    )
    score, gates = _apply_gates(90, [], match_result, mismatched_profile, job, DEFAULT_CONFIG)
    assert "role_family_mismatch" in gates
    assert score <= DEFAULT_CONFIG.gates.role_family_mismatch_cap

    matching_profile = CandidateProfile(
        roles=[Role(title="Backend Engineer", family="backend"), Role(title="QA Engineer", family="qa")],
        seniority=Seniority.MID,
    )
    match_result2 = MatchResult(must_have_matches=[], role_family_match=True, seniority_gap=0)
    score2, gates2 = _apply_gates(90, [], match_result2, matching_profile, job, DEFAULT_CONFIG)
    assert "role_family_mismatch" not in gates2


def test_gates_take_the_minimum_when_multiple_are_triggered():
    """All four gates at once - the final score must respect the tightest
    (lowest) cap among them."""
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.DEGREE, text="PhD required"), strength=MatchStrength.NONE,
        ),
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.SKILL, text="Go", canonical="Go"), strength=MatchStrength.NONE,
        ),
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.SKILL, text="Rust", canonical="Rust"), strength=MatchStrength.NONE,
        ),
    ]
    match_result = MatchResult(must_have_matches=must_have_matches, role_family_match=False, seniority_gap=3)
    profile = CandidateProfile(roles=[Role(title="Marketing Manager", family="marketing")], seniority=Seniority.MID)
    job = JobRequirements(title="Principal Backend Engineer", seniority=Seniority.PRINCIPAL, role_family="backend")

    score, gates = _apply_gates(95, must_have_matches, match_result, profile, job, DEFAULT_CONFIG)

    assert set(gates) == {"unmet_hard_requirement", "missing_skills", "seniority_gap", "role_family_mismatch"}
    assert score <= min(
        DEFAULT_CONFIG.gates.unmet_hard_requirement_cap,
        DEFAULT_CONFIG.gates.missing_skills_cap,
        DEFAULT_CONFIG.gates.seniority_gap_cap,
        DEFAULT_CONFIG.gates.role_family_mismatch_cap,
    )
