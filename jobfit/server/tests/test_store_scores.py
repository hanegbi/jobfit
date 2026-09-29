"""Per-(job, profile) scores. One row per profile rather than parallel
score_default/score_infra columns, so a third CV is data, not a migration."""

from jobfit.store import companies, db, jobs, scores

NOW = "2026-09-30T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Dev", "url": "https://acme.com/1"}], NOW)
    return conn


def test_scores_round_trip_per_profile():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 82.5, "coverage": 0.7, "confidence": "full",
                                                 "matched": ["python"], "cache_key": "k1"}})
    stored = scores.scores_for_job(conn, "j1")
    assert stored["default"]["score"] == 82.5
    assert stored["default"]["matched"] == ["python"]
    assert stored["default"]["confidence"] == "full"


def test_writing_again_replaces_that_profiles_score_only():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 10, "cache_key": "k1"},
                                     "infra": {"score": 20, "cache_key": "k2"}})
    scores.write_scores(conn, "j1", {"default": {"score": 99, "cache_key": "k9"}})
    stored = scores.scores_for_job(conn, "j1")
    assert stored["default"]["score"] == 99 and stored["infra"]["score"] == 20


def test_a_job_with_no_row_for_a_profile_reports_no_cache_key():
    """The rescore gate reads cache keys through this; a missing profile row
    must read as "not scored", never as up to date."""
    conn = _conn()
    assert scores.scores_for_job(conn, "j1").get("default") is None
    scores.write_scores(conn, "j1", {"default": {"score": 1, "cache_key": "k1"}})
    assert scores.scores_for_job(conn, "j1")["default"]["cache_key"] == "k1"


def test_matched_survives_hebrew_and_punctuation():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 1, "matched": ["C++", "פייתון"], "cache_key": "k"}})
    assert scores.scores_for_job(conn, "j1")["default"]["matched"] == ["C++", "פייתון"]


def test_scores_for_removed_profiles_are_dropped():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 1, "cache_key": "k1"},
                                     "gone": {"score": 2, "cache_key": "k2"}})
    assert scores.drop_scores_for_missing_profiles(conn, {"default"}) == 1
    assert set(scores.scores_for_job(conn, "j1")) == {"default"}


def test_dropping_with_no_profiles_left_clears_every_score():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 1, "cache_key": "k1"}})
    assert scores.drop_scores_for_missing_profiles(conn, set()) == 1
    assert scores.scores_for_job(conn, "j1") == {}
