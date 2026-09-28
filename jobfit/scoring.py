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
import json
import re
from functools import lru_cache
from pathlib import Path

from jobfit import config
from jobfit.ats_scorer import cv_extractor, jd_extractor, matcher
from jobfit.ats_scorer import scorer as ats_scorer_engine
from jobfit.ats_scorer.config import DEFAULT_CONFIG
from jobfit.ats_scorer.models import JobRequirements, MatchStrength

# Same threshold concept as update_jobs.MIN_DESCRIPTION_LEN - below this,
# there's not enough job text for the match to mean much, so the UI is told
# "title_only" rather than "full" confidence.
MIN_DESCRIPTION_LEN_FOR_FULL_CONFIDENCE = 50


def _compute_scoring_engine_fingerprint() -> str:
    """sha256 over every file that can change a job's score for the same
    inputs: this module, every ats_scorer source file, every ats_scorer
    taxonomy/config data file, and the scoring config's own serialized
    values. Computed once at import time - a scoring-formula fix changes
    this on the next process start, which is what makes
    score_cache_key() below self-invalidate without anyone having to
    remember to pass force=True."""
    hasher = hashlib.sha256()
    ats_scorer_dir = Path(__file__).parent / "ats_scorer"
    paths = [Path(__file__)]
    paths.extend(sorted(ats_scorer_dir.glob("*.py")))
    paths.extend(sorted((ats_scorer_dir / "data").glob("*.json")))
    for path in paths:
        hasher.update(path.read_bytes())
    hasher.update(DEFAULT_CONFIG.model_dump_json().encode("utf-8"))
    return hasher.hexdigest()[:12]


SCORING_ENGINE_FINGERPRINT = _compute_scoring_engine_fingerprint()


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


def _looks_unparseable(job_req: JobRequirements, evidence: dict | None = None) -> bool:
    """Whether a job's text yielded no structured signal at all - no
    must_have, no nice_to_have, and no title that itself reads as a real
    role.

    Each individual "nothing stated" default elsewhere (e.g. must_have
    coverage defaults to 100 when a JD simply has no formal Requirements
    section) is reasonable on its own, but a JD that is entirely
    unparseable would otherwise stack every one of those defaults into a
    misleadingly high score. Real case: a company's "careers" page
    returning marketing/product copy instead of a real posting extracts
    nothing at all - scoring that as a neutral/default match would
    silently defeat every "0 score = not a real job" gate built around
    scoring elsewhere in the pipeline (e.g. the evidence check above).

    domain deliberately does NOT count as parseable signal on its own -
    it's a single skills-taxonomy keyword hit anywhere in the text, and a
    long marketing page is likely to mention *some* tech word in passing
    (real case caught live: a company's "About Us" page - title "About
    Us", zero requirements, zero role-family match - still had "Deep
    Learning" appear once deep in its content and scored 62/100 "100% req
    match" because that alone was enough to skip this guard before).

    job_req.role_family is checked from title+body together and has the
    same weakness: a company docs/product page ("OpenTelemetry", "Code
    Governance & Compliance" - real titles caught live, both scraped as
    "jobs" from a docs URL and a marketing URL respectively) is often full
    of genuinely technical body text that happens to hit a family's
    keyword list (both classified as "backend"/"sales" from body content
    alone), even though the title itself obviously isn't a job title. The
    gate re-derives role family from the title ALONE, which is a much more
    reliable "is this actually a job posting" signal than a keyword scan
    over an entire scraped page.

    When scrape-time evidence is present (job_evidence on the job dict -
    see jobfit.scrape.enrich), a page with no JSON-LD JobPosting, no apply
    CTA, no requirement sections, and no role family derivable from its own
    title is unparseable regardless of what its body text happens to
    contain - this is the evidence-based replacement for the old CV-score
    tier gates in update_jobs.py."""
    if evidence:
        has_signal = any([
            evidence.get("jsonld_jobposting"), evidence.get("apply_cta"),
            (evidence.get("requirement_sections") or 0) >= 1, evidence.get("role_family_from_title"),
        ])
        if not has_signal:
            return True
    if job_req.must_have or job_req.nice_to_have:
        return False
    from jobfit.ats_scorer.taxonomy import load_role_families
    return load_role_families().classify(job_req.title) is None


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
    built-in hash(), which is randomized per-process) short hash of
    everything a job's score against one profile is actually computed
    from: the current scoring engine's own fingerprint (so a scoring-code
    or taxonomy-data change invalidates every cached score automatically,
    with no force=True needed), the job's title/description/department/
    location/employment_type (everything score_job's extraction and
    matching actually reads), and that profile's CV text.
    update_jobs.recompute_stage stores this per job/profile pair and
    skips rescoring when it's unchanged, so a rerun only does real work
    for jobs whose description changed (a rescrape), whose CV changed (a
    re-upload), or whose scoring logic changed (a code fix) - not every
    job every time."""
    cv_text = _cv_text_for_profile(profile)
    parts = [
        SCORING_ENGINE_FINGERPRINT,
        job.get("title") or "",
        job.get("description") or "",
        job.get("department") or "",
        job.get("location") or "",
        job.get("employment_type") or "",
        cv_text,
    ]
    evidence = job.get("job_evidence")
    if evidence:
        parts.append(json.dumps(evidence, sort_keys=True))
    combined = "\x00".join(parts).encode("utf-8")
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

    if _looks_unparseable(job_req, job.get("job_evidence")):
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
