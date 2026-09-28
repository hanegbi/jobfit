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


def test_score_job_treats_a_stray_domain_keyword_as_still_unparseable():
    """Real bug caught live in production: a company's "About Us" page
    (title "About Us", zero requirements, zero role-family match) still
    scored 62/100 "100% req match" because it happened to mention "Deep
    Learning" once in a long block of marketing copy, and a bare domain
    hit alone used to be enough to skip the zero-score guard. domain is a
    single skills-taxonomy keyword found anywhere in the text - too weak a
    signal on its own to call a page "a real job description"."""
    job = {
        "title": "About Us",
        "description": (
            "About Us Our Vision Our Team Newsroom Careers Resources Podcast Insights "
            "Featured Articles Resource Library Video Library Getting to Know Us Contact Us "
            "Defense and National Security Critical Infrastructure and Enterprise Government "
            "Agencies powered by Deep Learning and advanced analytics for mission-critical teams."
        ),
        "department": None, "employment_type": None,
    }
    profiles = {"default": {"must_have_keywords": [], "text": (
        "Senior Backend Engineer\nAcme | 2020 - Present\n- Built Python services on Kubernetes and AWS at scale"
    )}}

    result = scoring.score_job_both(job, profiles)

    assert result["score_default"] == 0


def test_score_job_treats_a_docs_page_with_a_body_only_role_family_hit_as_unparseable():
    """Real bug caught live in production: a company's docs page ("OpenTelemetry
    | Getting Started...") and a product/marketing page ("Code Governance &
    Compliance...") were both scraped as "jobs" and both scored 68/100 "100%
    req match", because their long technical body text happened to hit a
    role-family's keyword list ("backend", "sales") even though must_have and
    nice_to_have were both empty and the title itself obviously isn't a job
    title. role_family from title+body is too weak a signal here - the gate
    now re-derives it from the title alone."""
    job = {
        "title": "OpenTelemetry",
        "description": (
            "Getting started | Docs Skip to main content Search Theme Dark Light "
            "User guides Integrations OpenTelemetry Getting started Standalone "
            "installation Configuration options Instrumentation options Kubernetes "
            "server API backend integration guide for engineering teams."
        ),
        "department": None, "employment_type": None,
    }
    profiles = {"default": {"must_have_keywords": [], "text": (
        "Senior Backend Engineer\nAcme | 2020 - Present\n- Built Python services on Kubernetes and AWS at scale"
    )}}

    result = scoring.score_job_both(job, profiles)

    assert result["score_default"] == 0


def test_empty_scrape_evidence_zero_scores_even_a_parseable_looking_body():
    """A marketing page can contain enough incidental structure to extract a
    requirement or two; when the scrape-time evidence says the page had no
    JSON-LD, no apply CTA, no requirement sections and a title with no role
    family, the job is not a job."""
    job = {"title": "Code Governance and Compliance", "description": "Requirements: 5+ years of Python and Kubernetes.",
           "job_evidence": {"jsonld_jobposting": False, "apply_cta": False, "requirement_sections": 0, "role_family_from_title": None, "url_shape": "x||1"}}
    result = scoring.score_job(job, cv_text="Backend Engineer\nAcme | 2020 - Present\n- Python, Kubernetes")
    assert result["score"] == 0
    assert result["confidence"] == "title_only"


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
