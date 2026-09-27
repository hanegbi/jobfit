"""jobfit.scoring is now a thin adapter over jobfit.ats_scorer (rules-based,
CV-text-driven matching - no hardcoded role/title list). These tests lock
in the adapter's external contract (the dict shape update_jobs.py/
build_html.py depend on) and that a job genuinely scores differently
depending on what's actually in the CV text, not a fixed role list."""

from jobfit import scoring


def test_score_job_both_scores_identical_cv_text_identically():
    job = {"title": "Platform Engineer", "description": "", "department": None, "employment_type": None}
    profiles = {
        "a": {"must_have_keywords": [], "text": "Platform Engineer\nAcme | 2020 - Present\n- Ran Kubernetes infrastructure"},
        "b": {"must_have_keywords": [], "text": "Platform Engineer\nAcme | 2020 - Present\n- Ran Kubernetes infrastructure"},
    }
    result = scoring.score_job_both(job, profiles)
    assert result["score_a"] == result["score_b"]


def test_score_job_both_differentiates_profiles_by_their_own_cv_text():
    """A CV whose text actually overlaps with the job's own description
    scores higher than one that doesn't - driven purely by the CV's own
    words, not a hardcoded role list."""
    job = {
        "title": "Platform Engineer",
        "description": "Requirements:\n- 5+ years experience with Python\n- Kubernetes required\n- Terraform required",
        "department": None, "employment_type": None,
    }
    profiles = {
        "matches": {"must_have_keywords": [], "text": (
            "Senior Platform Engineer\nAcme Corp | 2018 - Present\n"
            "- Built and owned production infrastructure in Python on Kubernetes and Terraform"
        )},
        "no_match": {"must_have_keywords": [], "text": (
            "Java Developer\nFoo Inc | 2018 - Present\n"
            "- Built enterprise banking systems in Java with Spring Boot"
        )},
    }

    result = scoring.score_job_both(job, profiles)

    assert result["score_matches"] > result["score_no_match"]
    assert set(result["matched_matches"]) >= {"Python", "Kubernetes", "Terraform"}
    assert not {"Python", "Kubernetes", "Terraform"} & set(result["matched_no_match"])
    assert result["confidence_matches"] == "full"
    assert result["best_cv"] == "matches"


def test_score_job_both_handles_no_registered_profiles_without_crashing():
    """Real bug this locks in: uploading a referral job before any CV profile
    is registered called score_job_both(job, {}) and crashed with
    ValueError: max() iterable argument is empty (max() over an empty dict)."""
    job = {"title": "Backend Engineer", "description": "python", "department": None, "employment_type": None}

    result = scoring.score_job_both(job, {})

    assert result == {"best_cv": None, "best_score": 0, "best_confidence": None}


def test_score_job_both_falls_back_to_synthesized_text_when_profile_has_no_text():
    """Callers built against the old must_have_keywords-only profile shape
    (mostly tests elsewhere in this suite) must not crash - a keywords-only
    profile synthesizes a minimal CV text rather than scoring against
    nothing at all."""
    job = {"title": "Backend Engineer", "description": "Requirements:\n- Python required", "department": None, "employment_type": None}
    profiles = {"default": {"must_have_keywords": ["python"]}}

    result = scoring.score_job_both(job, profiles)

    assert "score_default" in result
    assert isinstance(result["score_default"], int)


def test_score_job_treats_a_completely_unparseable_job_as_zero():
    """A job whose text yields no must_have, no nice_to_have, no role
    family, and no domain (e.g. marketing/product-page junk mistakenly
    scraped as a "job") must score exactly 0 - update_jobs._any_job_scores_positive
    and the whole fetch-cascade junk-detection logic depend on 0 meaning
    "nothing real here", not a default/neutral credit."""
    job = {
        "title": "AI Security Suite Overview",
        "description": "Learn more about our product and how it protects your business at scale.",
        "department": None, "employment_type": None,
    }
    profiles = {"default": {"must_have_keywords": [], "text": (
        "Senior Backend Engineer\nAcme | 2020 - Present\n- Built Python services on Kubernetes and AWS at scale"
    )}}

    result = scoring.score_job_both(job, profiles)

    assert result["score_default"] == 0


def test_score_job_an_unrelated_job_scores_lower_than_a_relevant_one():
    """No exclude-keyword list needed: a sales job with a real requirements
    section naturally scores lower against a backend-engineering CV
    because the text just doesn't overlap - the engine's own hard gates
    (role-family mismatch) do this work now, not a hardcoded exclude list."""
    cv_text = (
        "Senior Backend Engineer\nAcme Corp | 2018 - Present\n"
        "- Built and owned distributed systems in Python and Kubernetes on our cloud infrastructure"
    )
    profiles = {"default": {"must_have_keywords": [], "text": cv_text}}

    relevant_job = {
        "title": "Backend Engineer",
        "description": "Requirements:\n- Python required\n- Kubernetes required",
        "department": None, "employment_type": None,
    }
    unrelated_job = {
        "title": "Account Executive",
        "description": "Requirements:\n- Proven sales quota experience required\n- CRM software required",
        "department": None, "employment_type": None,
    }

    relevant_result = scoring.score_job_both(relevant_job, profiles)
    unrelated_result = scoring.score_job_both(unrelated_job, profiles)

    assert relevant_result["score_default"] > unrelated_result["score_default"]
