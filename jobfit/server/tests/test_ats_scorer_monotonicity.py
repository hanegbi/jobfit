"""Monotonicity: adding a matched must_have requirement never lowers the
score; removing one never raises it. This is what makes the score
trustworthy as "more evidence, better or equal outcome" rather than a
black box that can move in surprising directions."""

import datetime

from jobfit.ats_scorer.pipeline import score_cv_against_job

_NOW = datetime.date(2026, 1, 1)

_JD = """Requirements:
- Experience with Python required
- Required: Kubernetes experience
- Must have AWS experience
- Required: Terraform experience
"""

_BASE_CV = """Jordan Blake
Tel Aviv, Israel

Backend Engineer
Acme Corp | 2021 - Present
- Built REST APIs in Python
"""

_CV_WITH_ONE_MORE_MATCH = """Jordan Blake
Tel Aviv, Israel

Backend Engineer
Acme Corp | 2021 - Present
- Built REST APIs in Python and deployed them on AWS
"""

_CV_WITH_TWO_MORE_MATCHES = """Jordan Blake
Tel Aviv, Israel

Backend Engineer
Acme Corp | 2021 - Present
- Built REST APIs in Python, deployed on AWS, and ran them on Kubernetes
"""


def _score(cv: str) -> int:
    return score_cv_against_job(cv, _JD, job_title="Backend Engineer", now=_NOW).score


def test_adding_a_matched_must_have_never_lowers_the_score():
    base = _score(_BASE_CV)
    one_more = _score(_CV_WITH_ONE_MORE_MATCH)
    two_more = _score(_CV_WITH_TWO_MORE_MATCHES)

    assert one_more >= base
    assert two_more >= one_more


def test_removing_a_matched_must_have_never_raises_the_score():
    two_matches = _score(_CV_WITH_TWO_MORE_MATCHES)
    one_match = _score(_CV_WITH_ONE_MORE_MATCH)
    no_extra_matches = _score(_BASE_CV)

    assert one_match <= two_matches
    assert no_extra_matches <= one_match


def test_adding_a_matched_nice_to_have_never_lowers_the_score():
    jd = """Requirements:
- Experience with Python required

Nice to have:
- AWS experience is a plus
"""
    without_aws = score_cv_against_job(
        "Backend Engineer\nAcme Corp | 2021 - Present\n- Built REST APIs in Python",
        jd, job_title="Backend Engineer", now=_NOW,
    ).score
    with_aws = score_cv_against_job(
        "Backend Engineer\nAcme Corp | 2021 - Present\n- Built REST APIs in Python and deployed on AWS",
        jd, job_title="Backend Engineer", now=_NOW,
    ).score
    assert with_aws >= without_aws


def test_stronger_evidence_for_the_same_skill_never_lowers_the_score():
    """Skills-section-only (weak) evidence should never outscore role-bullet
    (strong) evidence for the same skill."""
    jd = "Requirements:\n- Kubernetes experience required\n"
    weak = score_cv_against_job(
        "Backend Engineer\nAcme Corp | 2021 - Present\n- Built services\n\nSkills: Kubernetes",
        jd, job_title="Backend Engineer", now=_NOW,
    ).score
    strong = score_cv_against_job(
        "Backend Engineer\nAcme Corp | 2021 - Present\n- Deployed and managed services on Kubernetes",
        jd, job_title="Backend Engineer", now=_NOW,
    ).score
    assert strong >= weak
