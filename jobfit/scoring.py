"""Deterministic scoring of a job against a CV, via the rules-based
ATS-style engine in jobfit.ats_scorer (section-aware requirement
extraction, taxonomy-based skill matching, weighted sub-scores, hard
gates) - not a hardcoded list of "acceptable" role titles for one
specific person. A different CV drives a genuinely different match,
the way a real ATS keyword-matches a resume against a posting.

The location helpers below (is_relevant_location, canonical_city, ...)
are unrelated to the role/title-matching mechanism this module used to
hardcode and are unchanged.
"""

import hashlib
import re
from functools import lru_cache

from jobfit import config
from jobfit.ats_scorer import cv_extractor, jd_extractor, matcher
from jobfit.ats_scorer import scorer as ats_scorer_engine
from jobfit.ats_scorer.models import JobRequirements, MatchStrength

# Same threshold concept as update_jobs.MIN_DESCRIPTION_LEN - below this,
# there's not enough job text for the match to mean much, so the UI is told
# "title_only" rather than "full" confidence.
MIN_DESCRIPTION_LEN_FOR_FULL_CONFIDENCE = 50


def _word_match(term: str, text: str) -> bool:
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def is_relevant_location(location: str | None) -> bool:
    """Israel-based or remote; empty locations are kept (assume unspecified = ok)."""
    if not location:
        return True
    if any(_word_match(t, location) for t in config.REMOTE_TERMS):
        return True
    return any(_word_match(t, location) for t in config.ISRAEL_LOCATION_TERMS)


def is_remote_location(location: str | None) -> bool:
    if not location:
        return False
    return any(_word_match(t, location) for t in config.REMOTE_TERMS)


def canonical_city(location: str | None) -> str | None:
    if not location:
        return None
    for city, aliases in config.CITY_ALIASES.items():
        if any(_word_match(a, location) for a in aliases):
            return city.title()
    return None


_HEBREW_RE = re.compile(r"[֐-׿]")


def to_english_location(location: str | None) -> str | None:
    """Return an English-only version of a location string.

    Prefers a recognized Israeli city (from CITY_ALIASES); falls back to the
    raw string when it's already Latin-script; otherwise (unmapped Hebrew)
    falls back to the generic "Israel" label rather than showing Hebrew text.
    """
    if not location:
        return None
    if is_remote_location(location):
        return "Remote"
    city = canonical_city(location)
    if city:
        return city
    if _HEBREW_RE.search(location):
        return "Israel"
    return location.strip()


def required_years(text: str) -> int | None:
    """Overall years-of-experience the text states, via the same rules the
    ats_scorer JD extractor uses (kept as a thin wrapper so
    aggregate_to_jobs_v2's years_required UI field has one implementation,
    not two)."""
    from jobfit.ats_scorer.jd_extractor import _extract_required_years_total, _split_sections
    return _extract_required_years_total(_split_sections(text or ""))


def title_is_relevant(title: str | None) -> bool:
    """Whether a title maps to any recognized role family - a generic,
    data-file-driven check (jobfit/ats_scorer/data/role_families.json),
    not a hardcoded list of "acceptable" titles for one person. Used only
    by the older, superseded pipeline.py as a cheap pre-filter before
    fetching a description."""
    from jobfit.ats_scorer.taxonomy import load_role_families
    if not title:
        return False
    return load_role_families().classify(title) is not None


@lru_cache(maxsize=16)
def _cached_candidate_profile(cv_text: str):
    """CV extraction is real regex/date-range parsing work, and the same
    CV gets scored against every job in a run (thousands of times) - cache
    per CV text so it's only actually parsed once per process."""
    return cv_extractor.extract_candidate_profile(cv_text)


def _looks_unparseable(job_req: JobRequirements) -> bool:
    """Whether a job's text yielded no structured signal at all - no
    must_have, no nice_to_have, no recognizable role family.

    Each individual "nothing stated" default elsewhere (e.g. must_have
    coverage defaults to 100 when a JD simply has no formal Requirements
    section) is reasonable on its own, but a JD that is entirely
    unparseable would otherwise stack every one of those defaults into a
    misleadingly high score. Real case: a company's "careers" page
    returning marketing/product copy instead of a real posting extracts
    nothing at all - scoring that as a neutral/default match would
    silently defeat every "0 score = not a real job" gate built around
    scoring elsewhere in the pipeline (e.g. update_jobs._any_job_scores_positive).

    domain deliberately does NOT count as parseable signal on its own -
    it's a single skills-taxonomy keyword hit anywhere in the text, and a
    long marketing page is likely to mention *some* tech word in passing
    (real case caught live: a company's "About Us" page - title "About
    Us", zero requirements, zero role-family match - still had "Deep
    Learning" appear once deep in its content and scored 62/100 "100% req
    match" because that alone was enough to skip this guard before)."""
    return not (job_req.must_have or job_req.nice_to_have or job_req.role_family)


def _cv_text_for_profile(profile: dict) -> str:
    """The profile's full CV text when available (the normal case - see
    cv.py's build_profile); falls back to synthesizing a minimal "skills
    section" from must_have_keywords for callers (mostly tests) built
    against the old must_have_keywords-only profile shape."""
    text = profile.get("text")
    if text:
        return text
    keywords = profile.get("must_have_keywords") or []
    return f"Skills: {', '.join(keywords)}" if keywords else ""


def score_cache_key(job: dict, profile: dict) -> str:
    """Deterministic (stable across processes and runs - unlike Python's
    built-in hash(), which is randomized per-process) short hash of what a
    job's score against one profile was actually computed from: the job's
    own description text plus that profile's CV text. update_jobs.
    recompute_stage stores this per job/profile pair and skips rescoring
    when it's unchanged, so a rerun only does real work for jobs whose
    description changed (a rescrape) or whose CV changed (a re-upload),
    not every job every time."""
    description = job.get("description") or ""
    cv_text = _cv_text_for_profile(profile)
    combined = f"{description}\x00{cv_text}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]


def score_job(job: dict, cv_text: str = "", context: JobRequirements | None = None) -> dict:
    """Score one job 0-100 against one CV's text.

    job needs: title, description, department, location, employment_type.
    `context` is the profile-independent JobRequirements extraction - pass
    it in when scoring the same job against multiple profiles
    (score_job_both does) so the job text is only parsed once, not once
    per profile.

    Returns {score, confidence, matched, requirements, coverage_pct, notes}:
      - confidence "full" when the job has a real description to compare
        against, "title_only" when it's just a title (e.g. techmap fallback).
      - matched: canonical skill names the CV and job both share (chips
        for the UI - a subset of what actually drove the score, which also
        weighs title/seniority fit, experience relevance, and evidence depth).
      - requirements: the job's own must-have requirement texts.
    """
    title = job.get("title") or ""
    description = job.get("description") or ""
    job_req = context if context is not None else jd_extractor.extract_job_requirements(description, title=title)

    if _looks_unparseable(job_req):
        return {
            "score": 0, "confidence": "title_only", "matched": [],
            "requirements": [], "coverage_pct": None,
            "notes": ["job text yielded no structured requirements to score against"],
        }

    profile = _cached_candidate_profile(cv_text)
    match_result = matcher.match(profile, job_req)
    result = ats_scorer_engine.score(profile, job_req, match_result)

    confidence = "full" if len(description) >= MIN_DESCRIPTION_LEN_FOR_FULL_CONFIDENCE else "title_only"
    all_matches = match_result.must_have_matches + match_result.nice_to_have_matches
    matched_skills = [
        m.requirement.canonical for m in all_matches
        if m.strength != MatchStrength.NONE and m.requirement.canonical
    ]
    requirements = [m.requirement.text for m in match_result.must_have_matches]

    return {
        "score": result.score,
        "confidence": confidence,
        "matched": matched_skills,
        "requirements": requirements,
        "coverage_pct": result.sub_scores["must_have_coverage"].score,
        "notes": result.top_gaps,
    }


def score_job_both(job: dict, profiles: dict[str, dict]) -> dict:
    """Return score/coverage fields for every profile plus a best-of pick.

    No registered profiles (e.g. a referral job uploaded before any CV
    exists yet) is a real, reachable state, not an error - return a neutral
    result rather than crash; recompute_stage() fills in real scores once a
    profile exists.
    """
    if not profiles:
        return {"best_cv": None, "best_score": 0, "best_confidence": None}
    title = job.get("title") or ""
    description = job.get("description") or ""
    job_req = jd_extractor.extract_job_requirements(description, title=title)
    result = {}
    for name, profile in profiles.items():
        outcome = score_job(job, cv_text=_cv_text_for_profile(profile), context=job_req)
        result[f"score_{name}"] = outcome["score"]
        result[f"matched_{name}"] = outcome["matched"]
        result[f"coverage_{name}"] = outcome["coverage_pct"]
        result[f"confidence_{name}"] = outcome["confidence"]
        result[f"requirements_{name}"] = outcome["requirements"]
    best_name = max(profiles, key=lambda n: result[f"score_{n}"])
    result["best_cv"] = best_name
    result["best_score"] = result[f"score_{best_name}"]
    result["best_confidence"] = result[f"confidence_{best_name}"]
    return result
