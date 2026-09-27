"""Determinism: the same CV/JD input produces the identical ScoreResult
every time - no randomness, no LLM, no dict-ordering-dependent flakiness."""

import datetime

from jobfit.ats_scorer.pipeline import score_cv_against_job

_NOW = datetime.date(2026, 1, 1)

_CV = """Jane Doe
Tel Aviv, Israel

Senior Backend Engineer
Acme Corp | 2020 - Present
- Designed and owned a distributed payments platform processing 2M+ transactions/day in production
- Led migration to Kubernetes on AWS, reducing infra costs by 30%
- Built REST APIs in Python with PostgreSQL

Backend Engineer
Foo Inc | 2017 - 2020
- Developed microservices in Python

Education: BSc Computer Science
"""

_JD = """Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience
- Must have AWS experience
- BSc degree required

Nice to have:
- PostgreSQL is a plus
"""


def test_scoring_the_same_input_twice_gives_an_identical_result():
    first = score_cv_against_job(_CV, _JD, job_title="Senior Backend Engineer", now=_NOW)
    second = score_cv_against_job(_CV, _JD, job_title="Senior Backend Engineer", now=_NOW)

    assert first.model_dump() == second.model_dump()


def test_scoring_is_deterministic_across_many_repeated_runs():
    results = [
        score_cv_against_job(_CV, _JD, job_title="Senior Backend Engineer", now=_NOW).model_dump()
        for _ in range(10)
    ]
    assert all(r == results[0] for r in results)
