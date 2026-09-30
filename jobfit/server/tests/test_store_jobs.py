"""The scrape diff in SQL: what is new, what is still there, what is gone.
These mirror test_diff_and_update.py's rules, which the file-based path
established and which must survive the move to a database."""

from jobfit.store import companies, db, jobs

NOW = "2026-09-30T10:00:00Z"
LATER = "2026-10-01T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    return conn


def _job(url, title="Senior Backend Engineer", **extra):
    return {"id": url.rsplit("/", 1)[-1], "title": title, "url": url, **extra}


def test_a_first_scrape_stores_every_job_as_new():
    conn = _conn()
    new, closed = jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    assert (new, closed) == (1, 0)
    row = jobs.get_job(conn, "1")
    assert row["status"] == "new" and row["first_seen"] == NOW and row["title"] == "Senior Backend Engineer"


def test_a_posted_date_from_an_ats_beats_the_moment_we_first_looked():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", posted_at="2026-08-01")], NOW)
    assert jobs.get_job(conn, "1")["first_seen"] == "2026-08-01"


def test_seeing_a_job_again_marks_it_seen_and_bumps_last_seen():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    new, closed = jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], LATER)
    row = jobs.get_job(conn, "1")
    assert (new, closed) == (0, 0)
    assert row["status"] == "seen" and row["first_seen"] == NOW and row["last_seen"] == LATER


def test_a_job_missing_from_the_scrape_is_closed_not_deleted():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1"), _job("https://acme.com/jobs/2")], NOW)
    new, closed = jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], LATER)
    assert (new, closed) == (0, 1)
    gone = jobs.get_job(conn, "2")
    assert gone["status"] == "closed" and gone["closed_at"] == LATER


def test_may_close_false_leaves_missing_jobs_open():
    """A fetch too weak to prove absence records the visit and closes nothing."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    new, closed = jobs.upsert_scraped(conn, "acme", [], LATER, may_close=False)
    assert closed == 0 and jobs.get_job(conn, "1")["status"] == "new"


def test_a_reappearing_closed_job_opens_again():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    jobs.upsert_scraped(conn, "acme", [], LATER)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], LATER)
    assert jobs.get_job(conn, "1")["status"] == "seen"


def test_a_stored_title_is_trimmed_but_never_replaced():
    """Same rule as the file path: a re-scrape may cut card metadata off a
    stored title, never rename the job."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer Full-time Tel Aviv")], NOW)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer")], LATER)
    assert jobs.get_job(conn, "1")["title"] == "Senior MLOps Engineer"
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Office Manager")], LATER)
    assert jobs.get_job(conn, "1")["title"] == "Senior MLOps Engineer"


def test_an_empty_stored_field_is_filled_but_a_populated_one_is_kept():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", location="Tel Aviv")], NOW)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", location="Haifa",
                                            description="Requirements: Python")], LATER)
    row = jobs.get_job(conn, "1")
    assert row["location"] == "Tel Aviv"
    assert row["description"] == "Requirements: Python"


def test_close_by_url_closes_open_jobs_and_records_the_reason():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    stats = jobs.close_by_url(conn, {"https://acme.com/jobs/1": "http 404"}, LATER)
    row = jobs.get_job(conn, "1")
    assert stats["jobs_closed"] == 1
    assert row["status"] == "closed" and row["closed_reason"] == "http 404"
    assert jobs.close_by_url(conn, {"https://acme.com/jobs/1": "http 404"}, LATER)["already_closed"] == 1


def test_close_by_url_ignores_urls_it_does_not_know():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    assert jobs.close_by_url(conn, {"https://elsewhere.com/x": "gone"}, LATER)["jobs_closed"] == 0
    assert jobs.get_job(conn, "1")["status"] == "new"


def test_the_search_index_follows_a_title_change():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer Tel Aviv")], NOW)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer")], LATER)
    assert conn.execute("SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH 'Aviv'").fetchall() == []
    assert len(conn.execute("SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH 'MLOps'").fetchall()) == 1


def test_jobs_for_company_returns_only_that_companys_jobs():
    conn = _conn()
    companies.upsert_company(conn, "beta", "Beta")
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    jobs.upsert_scraped(conn, "beta", [_job("https://beta.com/jobs/2")], NOW)
    assert [r["id"] for r in jobs.jobs_for_company(conn, "acme")] == ["1"]


def test_a_job_read_back_carries_its_evidence_as_a_dict():
    """job_evidence is stored as JSON text, and scoring reads it with .get().
    Handing back the raw string crashed an entire recompute run."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [
        _job("https://acme.com/jobs/1", job_evidence={"jsonld_jobposting": True}),
    ], NOW)
    from_iter = next(iter(jobs.iter_all(conn)))
    assert from_iter["job_evidence"] == {"jsonld_jobposting": True}
    assert jobs.all_with_company(conn)[0]["job_evidence"] == {"jsonld_jobposting": True}


def test_a_job_with_no_evidence_reads_back_as_none():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    assert next(iter(jobs.iter_all(conn)))["job_evidence"] is None


def test_every_stored_job_can_be_scored(store_conn):
    """The end-to-end shape check: whatever the store hands back must be
    acceptable to the scorer, which is where the evidence bug surfaced."""
    from jobfit import scoring
    from jobfit.store import companies as store_companies

    store_companies.upsert_company(store_conn, "acme", "Acme")
    jobs.upsert_scraped(store_conn, "acme", [
        {"id": "j1", "title": "Backend Engineer", "url": "u1", "description": "Requirements: Python",
         "job_evidence": {"jsonld_jobposting": True, "apply_cta": True}},
    ], NOW)
    job = next(iter(jobs.iter_all(store_conn)))
    scored = scoring.score_job_both(job, {"default": {"text": "python developer"}})
    assert scored["score_default"] is not None


def test_detail_carries_the_description_scores_and_company():
    from jobfit.store import scores as store_scores

    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [
        _job("https://acme.com/jobs/1", description="Requirements: Python"),
    ], NOW)
    store_scores.write_scores(conn, "1", {"default": {"score": 80, "matched": ["python"], "cache_key": "k"}})
    detail = jobs.detail(conn, "1")
    assert detail["description"] == "Requirements: Python"
    assert detail["company"] == "Acme"
    assert detail["scores"]["default"]["score"] == 80
    assert detail["scores"]["default"]["matched"] == ["python"]
    assert detail["state"] == {"liked": False, "hidden": False, "sent": False, "reached_out": False}


def test_detail_of_an_unknown_job_is_none():
    assert jobs.detail(_conn(), "nope") is None
