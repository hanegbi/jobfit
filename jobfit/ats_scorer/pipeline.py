"""Ties extraction, matching, and scoring into a single entry point."""

from datetime import date

from jobfit.ats_scorer import jd_extractor, matcher, scorer
from jobfit.ats_scorer.config import DEFAULT_CONFIG, ScoringConfig
from jobfit.ats_scorer.models import ScoreResult
from jobfit.ats_scorer.profile import build_profile


def score_cv_against_job(
    cv_text: str, jd_text: str, job_title: str | None = None,
    config: ScoringConfig = DEFAULT_CONFIG, now: date | None = None,
) -> ScoreResult:
    """Run the full pipeline: extract, match, and score a CV against a JD.

    Args:
        cv_text: The full CV body text.
        jd_text: The full job description body text.
        job_title: The job's title, if known separately from jd_text.
        config: Scoring configuration.
        now: Reference date for years/recency calculations. Defaults to today.

    Returns:
        The final ScoreResult.
    """
    now = now or date.today()
    job = jd_extractor.extract_job_requirements(jd_text, title=job_title)
    profile = build_profile(cv_text, config=config, reference_date=now)
    match_result = matcher.match(profile, job, config=config, now=now)
    return scorer.score(profile, job, match_result, config=config)
