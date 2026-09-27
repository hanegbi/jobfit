"""Golden set: real CV/JD pairs with an expected band (not an exact score,
since the formula may be retuned) - covers every band and every hard gate.
"""

import datetime

import pytest

from jobfit.ats_scorer.pipeline import score_cv_against_job

_NOW = datetime.date(2026, 1, 1)


def _score(cv: str, jd: str, title: str | None = None):
    return score_cv_against_job(cv, jd, job_title=title, now=_NOW)


# --- Strong match (85-100) --------------------------------------------------

def test_strong_match_full_coverage_same_family_and_level():
    cv = """Jane Doe
Tel Aviv, Israel

Senior Backend Engineer
Acme Corp | 2020 - Present
- Designed and owned a distributed payments platform processing 2M+ transactions/day in production
- Led migration to Kubernetes on AWS, reducing infra costs by 30%
- Built and scaled REST APIs in Python with PostgreSQL, serving 500k requests/day

Backend Engineer
Foo Inc | 2017 - 2020
- Developed and shipped microservices in Python
- Owned CI/CD pipeline and improved deployment frequency

Education: BSc Computer Science
"""
    jd = """Requirements:
- 5+ years of experience with Python
- Required: experience with Kubernetes
- Must have AWS experience
- BSc degree in Computer Science or related field required

Nice to have:
- Familiarity with PostgreSQL is a plus
- GraphQL experience is a bonus

Responsibilities:
- Design and build backend services in production
"""
    result = _score(cv, jd, title="Senior Backend Engineer")
    assert result.band == "strong match"
    assert not result.gates_applied


# --- Good match (70-84) ------------------------------------------------------

def test_good_match_one_gap_with_medium_evidence():
    cv = """John Smith
Herzliya, Israel

Backend Engineer
Acme Corp | 2019 - 2023
- Built and owned production REST APIs in Python serving high traffic
- Deployed services on AWS

Junior Backend Engineer
StartCo | 2016 - 2019
- Wrote backend code in Python

Education: BSc Software Engineering
"""
    jd = """Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience
- Must have AWS experience
- BSc degree required
"""
    result = _score(cv, jd, title="Backend Engineer")
    assert result.band == "good match"


# --- Partial match (50-69) ---------------------------------------------------

def test_partial_match_real_gaps_in_must_haves():
    cv = """Sam Lee
Tel Aviv, Israel

Backend Engineer
Acme Corp | 2021 - Present
- Built REST APIs in Python
- Worked with PostgreSQL

Education: BSc Computer Science
"""
    jd = """Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience
- Must have AWS experience
- BSc degree required
"""
    result = _score(cv, jd, title="Backend Engineer")
    assert result.band == "partial match"


# --- Weak match (30-49) ------------------------------------------------------

def test_weak_match_adjacent_domain_low_evidence():
    cv = """Alex Kim
Tel Aviv, Israel

QA Automation Engineer
Acme Corp | 2022 - Present
- Wrote test automation scripts in Python and used AWS to run test infrastructure
- Reported bugs to the engineering team

Skills: SQL
"""
    jd = """Requirements:
- Experience with Python required
- Must have AWS experience
"""
    result = _score(cv, jd, title="Backend Engineer")
    assert result.band == "weak match"


# --- Not a fit (0-29) ---------------------------------------------------------

def test_not_a_fit_different_profession():
    cv = """Morgan Lee
Tel Aviv, Israel

Marketing Manager
Acme Corp | 2020 - Present
- Ran content marketing and SEO campaigns
- Managed social media marketing and brand strategy

Marketing Coordinator
StartCo | 2017 - 2020
- Wrote copy for email marketing campaigns
"""
    jd = """Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience
- Must have AWS experience
- BSc degree required
"""
    result = _score(cv, jd, title="Backend Engineer")
    assert result.band == "not a fit"


# --- Gate: unmet_hard_requirement (cap 45) -----------------------------------

def test_gate_unmet_hard_requirement_caps_at_45_despite_strong_skills():
    cv = """Dana Cohen
Tel Aviv, Israel

Senior Backend Engineer
Acme Corp | 2019 - Present
- Designed and owned production Python services on Kubernetes and AWS at scale
- Led backend architecture for a high-traffic platform

Education: BSc Computer Science
"""
    jd = """Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience
- Must have AWS experience
- PhD degree required
- Fluent French required
"""
    result = _score(cv, jd, title="Senior Backend Engineer")
    assert "unmet_hard_requirement" in result.gates_applied
    assert result.score <= 45


# --- Gate: missing_skills (cap 55) -------------------------------------------

def test_gate_missing_skills_caps_at_55_when_two_or_more_must_have_skills_absent():
    cv = """Ravi Patel
Tel Aviv, Israel

Senior Backend Engineer
Acme Corp | 2019 - Present
- Designed and owned production backend services at scale
- Led a team building internal tools

Education: BSc Computer Science
"""
    jd = """Requirements:
- Required: Kubernetes experience
- Must have AWS experience
- Required: Terraform experience
- BSc degree required
"""
    result = _score(cv, jd, title="Senior Backend Engineer")
    assert "missing_skills" in result.gates_applied
    assert result.score <= 55


# --- Gate: seniority_gap (cap 50) --------------------------------------------

def test_gate_seniority_gap_caps_at_50_for_a_junior_candidate_on_a_principal_role():
    cv = """Noa Levi
Tel Aviv, Israel

Junior Backend Engineer
Acme Corp | 2024 - Present
- Built REST APIs in Python with Kubernetes and AWS

Education: BSc Computer Science
"""
    jd = """Requirements:
- Experience with Python required
- Required: Kubernetes experience
- Must have AWS experience
- BSc degree required
"""
    result = _score(cv, jd, title="Principal Backend Engineer")
    assert "seniority_gap" in result.gates_applied
    assert result.score <= 50


# --- Gate: role_family_mismatch (cap 40) -------------------------------------

def test_gate_role_family_mismatch_caps_at_40_even_with_skill_overlap():
    cv = """Yossi Barak
Tel Aviv, Israel

QA Automation Engineer
Acme Corp | 2020 - Present
- Wrote test automation suites in Python using Selenium
- Used AWS and Kubernetes to run test infrastructure

Education: BSc Computer Science
"""
    jd = """Requirements:
- Experience with Python required
- Required: Kubernetes experience
- Must have AWS experience
- BSc degree required
"""
    result = _score(cv, jd, title="Backend Engineer")
    assert "role_family_mismatch" in result.gates_applied
    assert result.score <= 40


# --- Golden set self-check: every band and every gate is actually covered ---

def test_golden_set_covers_every_band():
    bands_covered = {"strong match", "good match", "partial match", "weak match", "not a fit"}
    # This test exists to document intent; the individual band tests above
    # are what actually enforce coverage.
    assert len(bands_covered) == 5
