"""Extractor tests: fixed JD/CV snippets with expected structured output,
so a taxonomy/keyword-list change can never silently break parsing without
a test noticing."""

from jobfit.ats_scorer import cv_extractor, jd_extractor
from jobfit.ats_scorer.models import RequirementKind, Seniority

import datetime


# --- JD extractor -------------------------------------------------------

def test_jd_extractor_splits_must_have_and_nice_to_have_sections():
    jd = """Senior Backend Engineer

Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience

Nice to have:
- Familiarity with Go is a plus
"""
    result = jd_extractor.extract_job_requirements(jd)

    must_have_texts = [r.text for r in result.must_have]
    nice_to_have_texts = [r.text for r in result.nice_to_have]
    assert any("Python" in t for t in must_have_texts)
    assert any("Kubernetes" in t for t in must_have_texts)
    assert any("Go" in t for t in nice_to_have_texts)
    assert not any("Go" in t for t in must_have_texts)


def test_jd_extractor_hard_word_overrides_softener_in_must_have_section():
    jd = """Backend Engineer

Requirements:
- Familiarity with AWS is required
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert any("AWS" in r.text for r in result.must_have)
    assert not any("AWS" in r.text for r in result.nice_to_have)


def test_jd_extractor_extracts_required_years_total():
    jd = """Data Engineer

Requirements:
- At least 4 years of experience
- SQL required
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert result.required_years_total == 4


def test_jd_extractor_infers_seniority_from_title():
    result = jd_extractor.extract_job_requirements("Requirements:\n- Python required", title="Staff Software Engineer")
    assert result.seniority == Seniority.STAFF


def test_jd_extractor_infers_seniority_from_years_when_title_has_no_signal():
    jd = """Software Engineer

Requirements:
- 7+ years of experience with Java
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert result.seniority == Seniority.SENIOR


def test_jd_extractor_infers_role_family_from_title_and_responsibilities():
    jd = """Marketing Manager

Responsibilities:
- Own content marketing and campaign management strategy

Requirements:
- SEO experience required
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert result.role_family == "marketing"


def test_jd_extractor_never_invents_a_requirement_not_in_the_text():
    jd = """Backend Engineer

Requirements:
- Python required
"""
    result = jd_extractor.extract_job_requirements(jd)
    all_text = " ".join(r.text for r in result.must_have + result.nice_to_have)
    assert "kubernetes" not in all_text.lower()
    assert "aws" not in all_text.lower()


def test_jd_extractor_classifies_degree_requirement():
    jd = """Data Scientist

Requirements:
- MSc degree required
- Python required
"""
    result = jd_extractor.extract_job_requirements(jd)
    kinds = [r.kind for r in result.must_have]
    assert RequirementKind.DEGREE in kinds


# --- CV extractor ---------------------------------------------------------

_NOW = datetime.date(2026, 1, 1)


def test_cv_extractor_parses_roles_with_dates_and_bullets():
    cv = """Jane Doe

Senior Backend Engineer
Acme Corp | 2020 - Present
- Designed and owned distributed systems in production
- Built REST APIs in Python

Backend Engineer
Foo Inc | 2017 - 2020
- Developed microservices in Python
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 2
    assert profile.roles[0].title == "Senior Backend Engineer"
    assert profile.roles[0].company == "Acme Corp"
    assert profile.roles[0].start == "2020"
    assert profile.roles[0].end == "present"
    assert len(profile.roles[0].bullets) == 2


def test_cv_extractor_gives_strong_evidence_for_a_role_bullet_skill():
    cv = """Senior Backend Engineer
Acme Corp | 2023 - Present
- Built services in Python and Kubernetes
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    python_skill = next(s for s in profile.skills if s.canonical == "Python")
    assert python_skill.evidence_strength == "strong"
    assert python_skill.recency_years == 0


def test_cv_extractor_gives_weak_evidence_for_a_skills_section_only_skill():
    cv = """Senior Backend Engineer
Acme Corp | 2023 - Present
- Built services

Skills: Python, Rust, Golang
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    go_skill = next(s for s in profile.skills if s.canonical == "Go")
    assert go_skill.evidence_strength == "weak"


def test_cv_extractor_infers_seniority_from_most_recent_title():
    cv = """Staff Engineer
Acme Corp | 2022 - Present
- Led platform architecture
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert profile.seniority == Seniority.STAFF


def test_cv_extractor_extracts_education_and_languages():
    cv = """Jane Doe

Education: BSc Computer Science

Languages: Fluent in English, native Hebrew

Senior Engineer
Acme | 2020 - Present
- Built systems
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert profile.education
    assert profile.languages


def test_cv_extractor_recent_role_family_uses_the_two_most_recent_roles():
    cv = """Marketing Manager
Acme Corp | 2023 - Present
- Ran content marketing and SEO campaigns

QA Engineer
OldCo | 2015 - 2018
- Wrote test automation suites
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    family = cv_extractor.recent_role_family(profile)
    assert family == "marketing"
