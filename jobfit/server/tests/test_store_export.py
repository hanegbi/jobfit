"""What reaches the exported JSON: relevance filters, dedupe, run report."""

from jobfit.store import export


def _row(company="Acme", title="Senior Backend Engineer", url="https://acme.com/careers/be",
         city="Tel Aviv", location="Tel Aviv, Israel", work_mode=None, fetch_status="ok", posted_at=None):
    return dict(company=company, title=title, url=url, city=city, location=location,
                work_mode=work_mode, fetch_status=fetch_status, posted_at=posted_at)


def test_the_title_lists_decide_what_is_exported():
    assert export.title_verdict("Senior Backend Engineer") is None
    assert export.title_verdict("Staff Software Engineer") is None
    assert export.title_verdict("MLOps Engineer") is None
    assert export.title_verdict("Executive Sales Director") == "title drop-list"
    assert export.title_verdict("Product Designer") == "title drop-list"
    assert export.title_verdict("Project Manager") == "title drop-list"


def test_analyst_is_dropped_only_when_no_engineer_is_named():
    """The one conditional rule: "Data Analyst" is out, an analyst title that
    also says engineer is judged on the keep-list like anything else."""
    assert export.title_verdict("Data Analyst") == "analyst without engineer"
    assert export.title_verdict("Senior Detection Analyst") == "analyst without engineer"
    assert export.title_verdict("Analyst, Data Engineer") is None


def test_whole_word_matching_so_staffing_is_not_staff():
    assert export.title_verdict("Staffing Coordinator") == "not on the title keep-list"
    assert export.title_verdict("Staff Software Engineer") is None


def test_a_foreign_posting_is_dropped_but_an_unstated_location_is_kept():
    """City is never guessed: a posting that stated nothing is exported with
    city null rather than inheriting the company's HQ."""
    jobs, report = export.build([
        _row(title="Backend Engineer", city="London", location="London, United Kingdom"),
        _row(title="Backend Engineer", url="https://acme.com/careers/be2", city=None, location=None),
    ])
    assert report["dropped_by_location"] == 1
    assert len(jobs) == 1 and jobs[0]["city"] is None


def test_an_ats_link_beats_a_linkedin_one_for_the_same_job():
    jobs, report = export.build([
        _row(url="https://www.linkedin.com/jobs/view/123"),
        _row(url="https://www.comeet.com/jobs/acme/11.00A/senior-backend-engineer/22.B33"),
    ])
    assert report["deduped"] == 1
    assert len(jobs) == 1 and "comeet.com" in jobs[0]["url"]
    # And the other way round, so it is the ATS that wins and not the order.
    jobs, _ = export.build([
        _row(url="https://www.comeet.com/jobs/acme/11.00A/senior-backend-engineer/22.B33"),
        _row(url="https://www.linkedin.com/jobs/view/123"),
    ])
    assert "comeet.com" in jobs[0]["url"]


def test_company_variants_are_one_company_when_deduping():
    """"Brandlight" and "Brandlight AI Ltd." are the same employer."""
    jobs, report = export.build([
        _row(company="Brandlight", url="https://a.com/careers/x"),
        _row(company="Brandlight AI Ltd.", url="https://b.com/careers/x"),
    ])
    assert report["deduped"] == 1 and len(jobs) == 1


def test_tracking_params_do_not_split_a_job_in_two():
    jobs, _ = export.build([
        _row(url="https://acme.com/careers/be?utm_source=linkedin"),
        _row(url="https://acme.com/careers/be?t=1787123964592"),
    ])
    assert len(jobs) == 1


def test_an_agency_is_flagged_not_dropped():
    jobs, _ = export.build([_row(company="Medulla"), _row(company="Wiz", url="https://wiz.io/careers/be")])
    assert {j["company"]: j["is_agency"] for j in jobs} == {"Medulla": True, "Wiz": False}


def test_the_report_counts_every_reason_and_lists_bad_fetches():
    jobs, report = export.build([
        _row(),
        _row(title="Sales Director", url="https://acme.com/careers/sd"),
        _row(title="Backend Engineer", url="https://acme.com/careers/uk", city="London", location="London, UK"),
        _row(title="Platform Engineer", url="https://acme.com/careers/pe", fetch_status="blocked"),
    ])
    assert report["found"] == 4 and report["kept"] == 2
    assert report["dropped_by_title"] == 1 and report["dropped_by_location"] == 1
    assert [e["fetch_status"] for e in report["not_ok_fetch"]] == ["blocked"]


def test_every_exported_job_has_the_agreed_shape():
    jobs, _ = export.build([_row(work_mode="hybrid", posted_at="2026-10-01")])
    assert set(jobs[0]) == {"company", "title", "url", "city", "work_mode", "is_agency",
                            "fetch_status", "posted_date", "scraped_at"}
    assert jobs[0]["work_mode"] == "hybrid" and jobs[0]["posted_date"] == "2026-10-01"
