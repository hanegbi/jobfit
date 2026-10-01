"""build_profile()'s two new fields - family_affinity and signature_skills -
see jobfit/ats_scorer/profile.py and
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 3."""

from datetime import date

from jobfit.ats_scorer.config import DEFAULT_CONFIG
from jobfit.ats_scorer.models import CandidateProfile, Role, SkillEvidence
from jobfit.ats_scorer.profile import (
    apply_family_overrides, build_profile, family_affinity, signature_skills,
)

NOW = date(2026, 1, 1)


def test_a_current_role_gets_full_affinity_weight():
    roles = [Role(title="Backend Engineer", family="backend", start="2022", end="present")]
    affinity = family_affinity(roles, NOW, decay_years=6.0)
    assert affinity["backend"] == 1.0


def test_a_role_that_ended_exactly_at_the_decay_boundary_gets_zero_weight():
    roles = [Role(title="Backend Engineer", family="backend", start="2010", end="2020")]
    affinity = family_affinity(roles, NOW, decay_years=6.0)
    assert affinity["backend"] == 0.0


def test_two_families_split_affinity_by_weighted_duration():
    roles = [
        Role(title="Backend Engineer", family="backend", start="2024", end="present"),  # 2y, decay 1.0 -> weight 2
        Role(title="Frontend Engineer", family="frontend", start="2023", end="2024"),  # 1y, decay (1 - 2/6) -> weight 0.667
    ]
    affinity = family_affinity(roles, NOW, decay_years=6.0)
    assert affinity["backend"] > affinity["frontend"] > 0
    assert round(sum(affinity.values()), 4) == 1.0


def test_an_unclassified_role_contributes_no_weight_and_is_not_an_error():
    roles = [Role(title="Mystery Role", family=None, start="2024", end="present")]
    affinity = family_affinity(roles, NOW, decay_years=6.0)
    assert all(v == 0.0 for v in affinity.values())


def test_affinity_vector_covers_every_known_family_even_with_no_roles():
    affinity = family_affinity([], NOW, decay_years=6.0)
    assert "backend" in affinity and "sales" in affinity
    assert all(v == 0.0 for v in affinity.values())


def test_signature_skill_needs_both_strong_evidence_and_a_high_idf():
    skills = [
        SkillEvidence(canonical="Triton", evidence_strength="strong"),
        SkillEvidence(canonical="Triton Listed Only", evidence_strength="weak"),
        SkillEvidence(canonical="Python", evidence_strength="strong"),
    ]
    idf = {"Triton": 8.58, "Triton Listed Only": 8.58, "Python": 2.25}
    assert signature_skills(skills, idf, threshold=6.0) == ["Triton"]


def test_signature_skills_returns_nothing_below_the_threshold():
    skills = [SkillEvidence(canonical="Python", evidence_strength="strong")]
    assert signature_skills(skills, {"Python": 2.25}, threshold=6.0) == []


def test_build_profile_fills_in_family_affinity_and_signature_skills():
    cv_text = (
        "Experience\n\n"
        "Backend Engineer\n"
        "Acme Corp    2023 - present\n"
        "- Built distributed inference pipelines using ONNX runtime and NVIDIA Triton\n"
        "- Designed REST APIs in Python\n\n"
        "Skills\nPython, ONNX\n"
    )
    profile = build_profile(cv_text, reference_date=NOW)
    assert isinstance(profile, CandidateProfile)
    assert profile.family_affinity.get("backend") == 1.0
    assert "Distributed Inference" in profile.signature_skills
    assert "ONNX" in profile.signature_skills
    assert "Triton" in profile.signature_skills
    assert "Python" not in profile.signature_skills  # strong evidence, but common - below threshold


def test_build_profile_on_a_cv_with_no_roles_returns_a_zeroed_vector_not_an_error():
    profile = build_profile("Just a summary paragraph, no dated roles.", reference_date=NOW)
    assert profile.family_affinity == {} or all(v == 0.0 for v in profile.family_affinity.values())
    assert profile.signature_skills == []


# --- apply_family_overrides -----------------------------------------------

def test_apply_family_overrides_is_a_noop_with_no_overrides():
    profile = CandidateProfile(family_affinity={"backend": 1.0})
    assert apply_family_overrides(profile, {}) is profile


def test_block_zeroes_a_family_and_renormalizes_the_rest():
    profile = CandidateProfile(family_affinity={"backend": 0.6, "frontend": 0.4})
    result = apply_family_overrides(profile, {"frontend": "block"})
    assert result.family_affinity["frontend"] == 0.0
    assert result.family_affinity["backend"] == 1.0


def test_boost_raises_a_family_above_the_current_ceiling():
    profile = CandidateProfile(family_affinity={"backend": 0.6, "frontend": 0.4})
    result = apply_family_overrides(profile, {"frontend": "boost"})
    assert result.family_affinity["frontend"] > result.family_affinity["backend"]
    assert round(sum(result.family_affinity.values()), 4) == 1.0


def test_an_unknown_family_name_in_overrides_is_ignored_not_an_error():
    profile = CandidateProfile(family_affinity={"backend": 1.0})
    result = apply_family_overrides(profile, {"not_a_real_family": "boost"})
    assert result.family_affinity == {"backend": 1.0}


# --- the shared classifier on the CV side (section 3, point 2) ------------

def test_a_role_with_a_generic_title_classifies_from_its_bullets_via_jd_fallback():
    cv_text = (
        "Experience\n\n"
        "Software Engineer\n"
        "Acme Corp    2022 - present\n"
        "- Migrated the legacy jQuery frontend to React and TypeScript\n"
    )
    profile = build_profile(cv_text, reference_date=NOW)
    assert profile.roles[0].family == "frontend"
