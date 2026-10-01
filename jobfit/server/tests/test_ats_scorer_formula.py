"""The new score = family_fit x job_fit mechanics scorer.py adds beyond
the sub-score weighted sum: _signature_bonus() and
_negative_evidence_penalty(). See
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 5."""

from jobfit.ats_scorer.config import DEFAULT_CONFIG
from jobfit.ats_scorer.models import (
    CandidateProfile, JobRequirements, MatchedRequirement, MatchStrength, Requirement, RequirementKind, Seniority,
)
from jobfit.ats_scorer.scorer import _negative_evidence_penalty, _signature_bonus

ADJACENCY = {"backend": {"backend": 1.0, "data_science": 0.1}, "data_science": {"data_science": 1.0, "backend": 0.1}}


def _skill_req(name):
    return Requirement(kind=RequirementKind.SKILL, text=name, canonical=name)


# --- _signature_bonus -------------------------------------------------------

def test_signature_bonus_is_zero_below_two_matched_signature_skills():
    job = JobRequirements(title="t", seniority=Seniority.MID, must_have=[_skill_req("Triton")])
    profile = CandidateProfile(signature_skills=["Triton"])
    bonus, reason = _signature_bonus(job, profile, DEFAULT_CONFIG)
    assert bonus == 0.0 and reason is None


def test_signature_bonus_applies_at_two_or_more_matched_signature_skills():
    job = JobRequirements(
        title="t", seniority=Seniority.MID,
        must_have=[_skill_req("Triton")], nice_to_have=[_skill_req("ONNX")],
    )
    profile = CandidateProfile(signature_skills=["Triton", "ONNX", "Distributed Inference"])
    bonus, reason = _signature_bonus(job, profile, DEFAULT_CONFIG)
    assert bonus == DEFAULT_CONFIG.family_fit.signature_bonus
    assert "Triton" in reason and "ONNX" in reason


def test_signature_bonus_ignores_skills_the_job_never_asked_for():
    job = JobRequirements(title="t", seniority=Seniority.MID, must_have=[_skill_req("Python")])
    profile = CandidateProfile(signature_skills=["Triton", "ONNX"])
    bonus, reason = _signature_bonus(job, profile, DEFAULT_CONFIG)
    assert bonus == 0.0 and reason is None


# --- _negative_evidence_penalty ---------------------------------------------

def test_negative_evidence_penalizes_a_matched_requirement_from_a_non_adjacent_family():
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.DOMAIN, text="data scientist experience with statistics"),
            strength=MatchStrength.STRONG,
        ),
    ]
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")
    penalty, reasons = _negative_evidence_penalty(must_have_matches, job, ADJACENCY, DEFAULT_CONFIG)
    assert penalty == DEFAULT_CONFIG.family_fit.negative_evidence_penalty * DEFAULT_CONFIG.match_strength_weights.strong
    assert reasons


def test_negative_evidence_is_zero_when_the_requirement_matches_the_jobs_own_family():
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.DOMAIN, text="backend engineer with distributed systems"),
            strength=MatchStrength.STRONG,
        ),
    ]
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")
    penalty, reasons = _negative_evidence_penalty(must_have_matches, job, ADJACENCY, DEFAULT_CONFIG)
    assert penalty == 0.0 and reasons == []


def test_negative_evidence_is_zero_for_an_unmatched_requirement():
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.DOMAIN, text="data scientist with statistics"),
            strength=MatchStrength.NONE,
        ),
    ]
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")
    penalty, reasons = _negative_evidence_penalty(must_have_matches, job, ADJACENCY, DEFAULT_CONFIG)
    assert penalty == 0.0 and reasons == []


def test_negative_evidence_is_zero_when_the_job_itself_has_no_known_family():
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.DOMAIN, text="data scientist with statistics"),
            strength=MatchStrength.STRONG,
        ),
    ]
    job = JobRequirements(title="Mystery Role", seniority=Seniority.MID, role_family=None)
    penalty, reasons = _negative_evidence_penalty(must_have_matches, job, ADJACENCY, DEFAULT_CONFIG)
    assert penalty == 0.0 and reasons == []


def test_negative_evidence_is_zero_when_the_requirements_own_text_matches_no_family_at_all():
    """Most individual requirement clauses won't restate a title-shaped
    family phrase at all - the conservative, intended "never guess"
    outcome, not a bug."""
    must_have_matches = [
        MatchedRequirement(
            requirement=Requirement(kind=RequirementKind.SKILL, text="5+ years of Python", canonical="Python"),
            strength=MatchStrength.STRONG,
        ),
    ]
    job = JobRequirements(title="Backend Engineer", seniority=Seniority.MID, role_family="backend")
    penalty, reasons = _negative_evidence_penalty(must_have_matches, job, ADJACENCY, DEFAULT_CONFIG)
    assert penalty == 0.0 and reasons == []
