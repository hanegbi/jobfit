"""recompute_stage skips rescoring a (job, profile) pair whose inputs
haven't changed since last time - a rerun should only do real work for
jobs whose description changed (a rescrape) or whose CV changed (a
re-upload), not every job every time."""

import json

from jobfit import config, scoring
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


def test_score_cache_key_changes_when_the_scoring_engine_fingerprint_changes(monkeypatch):
    job = {"title": "Backend Engineer", "description": "Python required"}
    profile = {"text": "Backend engineer with Python experience"}
    key_before = scoring.score_cache_key(job, profile)

    monkeypatch.setattr(scoring, "SCORING_ENGINE_FINGERPRINT", "different000")
    key_after = scoring.score_cache_key(job, profile)

    assert key_before != key_after


def test_score_cache_key_changes_when_the_job_title_changes():
    profile = {"text": "Backend engineer with Python experience"}
    key1 = scoring.score_cache_key({"title": "Backend Engineer", "description": "Python required"}, profile)
    key2 = scoring.score_cache_key({"title": "Frontend Engineer", "description": "Python required"}, profile)
    assert key1 != key2


def test_score_cache_key_changes_when_department_location_or_employment_type_changes():
    profile = {"text": "Backend engineer with Python experience"}
    base = {"title": "Backend Engineer", "description": "Python required", "department": "R&D", "location": "Tel Aviv", "employment_type": "Full-time"}
    base_key = scoring.score_cache_key(base, profile)

    for field, new_value in [("department", "Sales"), ("location", "Haifa"), ("employment_type", "Part-time")]:
        changed = dict(base)
        changed[field] = new_value
        assert scoring.score_cache_key(changed, profile) != base_key, f"{field} change did not affect the cache key"


def test_score_cache_key_is_stable_for_identical_inputs_including_new_fields():
    job = {"title": "Backend Engineer", "description": "Python required", "department": "R&D", "location": "Tel Aviv", "employment_type": "Full-time"}
    profile = {"text": "Backend engineer with Python experience"}
    assert scoring.score_cache_key(job, profile) == scoring.score_cache_key(job, profile)


def test_score_cache_key_changes_when_job_evidence_is_present():
    profile = {"text": "Backend engineer with Python experience"}
    plain = {"description": "Python required"}
    with_evidence = {"description": "Python required", "job_evidence": {"jsonld_jobposting": True}}
    assert scoring.score_cache_key(plain, profile) != scoring.score_cache_key(with_evidence, profile)


# --- recompute over the store ------------------------------------------------

def _seed(conn, description="Requirements: Python"):
    from jobfit.store import companies as store_companies
    from jobfit.store import jobs as store_jobs

    store_companies.upsert_company(conn, "acme", "Acme")
    store_jobs.upsert_scraped(conn, "acme", [
        {"id": "j1", "title": "Backend Engineer", "url": "https://acme.com/1", "description": description},
        {"id": "j2", "title": "Data Scientist", "url": "https://acme.com/2", "description": "Requirements: pandas"},
    ], "2026-09-30T10:00:00Z")


def test_recompute_scores_every_job_then_skips_them_next_time(store_conn, monkeypatch):
    from jobfit.store import scores as store_scores

    _seed(store_conn)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python developer"}})
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)

    scored = []
    original = update_jobs.scoring.score_job_both
    monkeypatch.setattr(update_jobs.scoring, "score_job_both",
                        lambda job, profiles: scored.append(job["id"]) or original(job, profiles))

    update_jobs.recompute_stage()
    assert sorted(scored) == ["j1", "j2"]
    assert store_scores.scores_for_job(store_conn, "j1")["default"]["cache_key"]

    scored.clear()
    update_jobs.recompute_stage()
    assert scored == []   # nothing changed, so nothing was rescored


def test_recompute_rescores_only_the_job_whose_text_changed(store_conn, monkeypatch):
    from jobfit.store import scores as store_scores

    _seed(store_conn)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python developer"}})
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    update_jobs.recompute_stage()
    before = store_scores.scores_for_job(store_conn, "j1")["default"]["cache_key"]

    store_conn.execute("UPDATE jobs SET description = 'Requirements: Go' WHERE id = 'j1'")
    scored = []
    original = update_jobs.scoring.score_job_both
    monkeypatch.setattr(update_jobs.scoring, "score_job_both",
                        lambda job, profiles: scored.append(job["id"]) or original(job, profiles))
    update_jobs.recompute_stage()

    assert scored == ["j1"]
    assert store_scores.scores_for_job(store_conn, "j1")["default"]["cache_key"] != before


def test_recompute_drops_scores_for_a_removed_profile(store_conn, monkeypatch):
    from jobfit.store import scores as store_scores

    _seed(store_conn)
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    monkeypatch.setattr(update_jobs.cv, "load_profiles",
                        lambda: {"default": {"text": "python"}, "infra": {"text": "kubernetes"}})
    update_jobs.recompute_stage()
    assert set(store_scores.scores_for_job(store_conn, "j1")) == {"default", "infra"}

    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python"}})
    update_jobs.recompute_stage()
    assert set(store_scores.scores_for_job(store_conn, "j1")) == {"default"}


def test_force_rescores_even_when_nothing_changed(store_conn, monkeypatch):
    _seed(store_conn)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python developer"}})
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    update_jobs.recompute_stage()

    scored = []
    original = update_jobs.scoring.score_job_both
    monkeypatch.setattr(update_jobs.scoring, "score_job_both",
                        lambda job, profiles: scored.append(job["id"]) or original(job, profiles))
    update_jobs.recompute_stage(force=True)
    assert sorted(scored) == ["j1", "j2"]


def test_recompute_rescores_every_job_when_a_profile_is_added(store_conn, monkeypatch):
    from jobfit.store import scores as store_scores

    _seed(store_conn)
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python"}})
    update_jobs.recompute_stage()

    monkeypatch.setattr(update_jobs.cv, "load_profiles",
                        lambda: {"default": {"text": "python"}, "infra": {"text": "kubernetes"}})
    update_jobs.recompute_stage()
    assert set(store_scores.scores_for_job(store_conn, "j1")) == {"default", "infra"}


def test_recompute_updates_years_required_when_the_text_changed(store_conn, monkeypatch):
    from jobfit.store import jobs as store_jobs

    _seed(store_conn, description="Requirements: Python")
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python"}})
    update_jobs.recompute_stage()
    assert store_jobs.get_job(store_conn, "j1")["years_required"] is None

    store_conn.execute("UPDATE jobs SET description = '5+ years experience' WHERE id = 'j1'")
    update_jobs.recompute_stage()
    assert store_jobs.get_job(store_conn, "j1")["years_required"] == 5


def test_recompute_records_the_scoring_engine_for_the_pages_footer(store_conn, monkeypatch, tmp_path):
    from jobfit import scoring

    monkeypatch.setattr(update_jobs, "META_PATH", tmp_path / "_meta.json")
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python"}})
    _seed(store_conn)
    update_jobs.recompute_stage()
    assert update_jobs.load_meta()["scoring_engine"] == scoring.SCORING_ENGINE_FINGERPRINT


def test_recompute_with_no_profiles_leaves_stored_scores_alone(store_conn, monkeypatch):
    """Regression: recompute used to drop scores for "missing" profiles
    before checking whether any profile was registered at all, so one run
    with an unloaded CV registry wiped every score in the database."""
    from jobfit.store import scores as store_scores

    _seed(store_conn)
    monkeypatch.setattr(update_jobs, "_rebuild_page", lambda: None)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {"default": {"text": "python"}})
    update_jobs.recompute_stage()
    assert store_scores.scores_for_job(store_conn, "j1")["default"]["score"] is not None

    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})
    update_jobs.recompute_stage()
    assert store_scores.scores_for_job(store_conn, "j1")["default"]["score"] is not None
