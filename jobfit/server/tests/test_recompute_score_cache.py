"""recompute_stage skips rescoring a (job, profile) pair whose inputs
haven't changed since last time - a rerun should only do real work for
jobs whose description changed (a rescrape) or whose CV changed (a
re-upload), not every job every time."""

import json

from jobfit import scoring
from jobfit.scripts import update_jobs


def test_score_cache_key_is_stable_for_identical_inputs():
    job = {"description": "Python required"}
    profile = {"text": "Backend engineer with Python experience"}
    assert scoring.score_cache_key(job, profile) == scoring.score_cache_key(job, profile)


def test_score_cache_key_changes_when_the_job_description_changes():
    profile = {"text": "Backend engineer with Python experience"}
    key1 = scoring.score_cache_key({"description": "Python required"}, profile)
    key2 = scoring.score_cache_key({"description": "Java required"}, profile)
    assert key1 != key2


def test_score_cache_key_changes_when_the_cv_text_changes():
    job = {"description": "Python required"}
    key1 = scoring.score_cache_key(job, {"text": "Backend engineer with Python"})
    key2 = scoring.score_cache_key(job, {"text": "Frontend engineer with React"})
    assert key1 != key2


def test_recompute_skips_a_job_whose_cache_key_is_already_current(tmp_path, monkeypatch):
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    profiles = {"default": {"text": "Backend engineer with Python experience"}}
    job = {"id": "1", "title": "Backend Engineer", "description": "Python required"}
    job["_score_cache_keys"] = {"default": scoring.score_cache_key(job, profiles["default"])}
    job["score_default"] = 99  # a deliberately-wrong stored score, to prove it's left untouched
    path = tmp_path / "acme.json"
    path.write_text(json.dumps({"name": "Acme", "jobs": [job]}), encoding="utf-8")

    rescored, skipped = update_jobs._recompute_one_company(
        str(path), profiles, {"default"}, ("score_", "matched_", "coverage_", "confidence_", "requirements_"),
    )

    assert (rescored, skipped) == (0, 1)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["jobs"][0]["score_default"] == 99  # untouched, not recomputed


def test_recompute_rescores_a_job_whose_description_changed_since_it_was_scored(tmp_path, monkeypatch):
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    profiles = {"default": {"text": "Backend engineer with Python experience"}}
    job = {"id": "1", "title": "Backend Engineer", "description": "Java required"}
    # cache key computed from a *different*, older description - simulates a rescrape changing the text
    job["_score_cache_keys"] = {"default": scoring.score_cache_key({"description": "old text"}, profiles["default"])}
    job["score_default"] = 0
    path = tmp_path / "acme.json"
    path.write_text(json.dumps({"name": "Acme", "jobs": [job]}), encoding="utf-8")

    rescored, skipped = update_jobs._recompute_one_company(
        str(path), profiles, {"default"}, ("score_", "matched_", "coverage_", "confidence_", "requirements_"),
    )

    assert (rescored, skipped) == (1, 0)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["jobs"][0]["_score_cache_keys"] == {"default": scoring.score_cache_key(job, profiles["default"])}


def test_recompute_rescores_when_a_profile_is_added(tmp_path, monkeypatch):
    """Real case: uploading a second CV profile - a job already scored
    against "default" must also get scored against the new "infra"
    profile, not skipped just because "default"'s own key is unchanged."""
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    old_profiles = {"default": {"text": "Backend engineer with Python experience"}}
    job = {"id": "1", "title": "Backend Engineer", "description": "Python required"}
    job["_score_cache_keys"] = {"default": scoring.score_cache_key(job, old_profiles["default"])}
    path = tmp_path / "acme.json"
    path.write_text(json.dumps({"name": "Acme", "jobs": [job]}), encoding="utf-8")

    new_profiles = {
        "default": {"text": "Backend engineer with Python experience"},
        "infra": {"text": "Infra engineer with Kubernetes experience"},
    }
    rescored, skipped = update_jobs._recompute_one_company(
        str(path), new_profiles, {"default", "infra"},
        ("score_", "matched_", "coverage_", "confidence_", "requirements_"),
    )

    assert (rescored, skipped) == (1, 0)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "score_infra" in saved["jobs"][0]
