from jobfit import config, scoring


def test_score_job_defaults_to_shared_role_weights():
    job = {"title": "Platform Engineer", "description": "", "department": None, "employment_type": None}
    result = scoring.score_job(job, must_have_keywords=[])
    assert result["notes"][0] == "platform engineer"


def test_score_job_both_scores_every_profile_the_same_way_for_role_fit():
    job = {"title": "Platform Engineer", "description": "", "department": None, "employment_type": None}
    profiles = {
        "a": {"must_have_keywords": []},
        "b": {"must_have_keywords": []},
    }
    result = scoring.score_job_both(job, profiles)
    # No per-profile role-weight tuning left: identical must_have_keywords -> identical scores.
    assert result["score_a"] == result["score_b"]


def test_score_job_both_still_differentiates_profiles_by_their_own_skills():
    """Locks in current behavior before the score_job_both perf refactor (job-level
    work - role match, experience, requirements extraction - computed once and
    shared, since it no longer depends on which profile is being scored)."""
    job = {
        "title": "Platform Engineer",
        "description": "5+ years experience with Python, Kubernetes, and Terraform required.",
        "department": None, "employment_type": None,
    }
    profiles = {
        "matches": {"must_have_keywords": ["python", "kubernetes", "terraform"]},
        "no_match": {"must_have_keywords": ["java", "spring"]},
    }

    result = scoring.score_job_both(job, profiles)

    assert result["score_matches"] > result["score_no_match"]
    assert set(result["matched_matches"]) >= {"python", "kubernetes", "terraform"}
    assert not {"python", "kubernetes", "terraform"} & set(result["matched_no_match"])
    assert result["coverage_matches"] == 100
    assert result["confidence_matches"] == "full"
    assert result["confidence_no_match"] == "full"
    assert result["best_cv"] == "matches"
