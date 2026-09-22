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
