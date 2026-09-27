import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from jobfit import company_review, config
from jobfit.scripts import update_jobs


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_skips_a_company_checked_within_the_ttl():
    recent = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    assert update_jobs._should_skip_company({"last_checked": recent}, force=False) is True


def test_does_not_skip_a_stale_company():
    stale = _iso(datetime.now(timezone.utc) - timedelta(hours=config.COMPANY_RECHECK_TTL_HOURS + 1))
    assert update_jobs._should_skip_company({"last_checked": stale}, force=False) is False


def test_force_never_skips_even_if_recent():
    recent = _iso(datetime.now(timezone.utc))
    assert update_jobs._should_skip_company({"last_checked": recent}, force=True) is False


def test_never_checked_company_is_not_skipped():
    assert update_jobs._should_skip_company({}, force=False) is False
    assert update_jobs._should_skip_company({"last_checked": None}, force=False) is False


# --- load_companies_to_scrape ---------------------------------------------

@pytest.fixture
def _isolated_review_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")


def test_load_companies_to_scrape_includes_real_url_companies(_isolated_review_paths):
    config.COMPANIES_CAREER_PAGES_PATH.write_text(
        json.dumps({"Acme": "https://acme.com/careers"}), encoding="utf-8"
    )
    assert update_jobs.load_companies_to_scrape() == {"Acme": "https://acme.com/careers"}


def test_load_companies_to_scrape_excludes_unreviewed_null_url_companies(_isolated_review_paths):
    config.COMPANIES_CAREER_PAGES_PATH.write_text(json.dumps({"Beta": None}), encoding="utf-8")
    assert update_jobs.load_companies_to_scrape() == {}


def test_load_companies_to_scrape_excludes_skipped_companies(_isolated_review_paths):
    config.COMPANIES_CAREER_PAGES_PATH.write_text(json.dumps({"Beta": None}), encoding="utf-8")
    company_review.set_decision("Beta", "skip")
    assert update_jobs.load_companies_to_scrape() == {}


def test_load_companies_to_scrape_includes_techmap_approved_companies_as_none(_isolated_review_paths):
    config.COMPANIES_CAREER_PAGES_PATH.write_text(json.dumps({"Beta": None}), encoding="utf-8")
    company_review.set_decision("Beta", "techmap")
    assert update_jobs.load_companies_to_scrape() == {"Beta": None}


# --- fetch_company_jobs_async null-URL fallback ---------------------------

def test_fetch_company_jobs_async_uses_techmap_directly_when_url_is_none():
    techmap_index = {"beta": [{"title": "Backend Engineer", "location": "Remote", "url": "https://x", "company": "Beta"}]}
    jobs = asyncio.run(
        update_jobs.fetch_company_jobs_async("Beta", None, session=None, profiles={}, techmap_index=techmap_index)
    )
    assert jobs == [{"title": "Backend Engineer", "location": "Remote", "url": "https://x", "description": ""}]


# --- scrape_stage graceful cancellation ------------------------------------

def test_scrape_stage_queues_nothing_when_cancel_event_is_already_set(monkeypatch):
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    monkeypatch.setattr(update_jobs.ats_fetchers, "make_session", lambda: None)
    called = []

    def _fake_process(company, url, session, profiles, techmap_index, force):
        called.append(company)
        return company, 0, 0, False, None

    monkeypatch.setattr(update_jobs, "_process_company", _fake_process)

    cancel_event = threading.Event()
    cancel_event.set()

    stats = update_jobs.scrape_stage(
        {"Acme": "https://acme.com/careers", "Beta": "https://beta.com/careers"},
        profiles={}, cancel_event=cancel_event,
    )

    assert called == []
    assert stats.companies_checked == 0


def test_scrape_stage_runs_normally_with_no_cancel_event(monkeypatch):
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    monkeypatch.setattr(update_jobs.ats_fetchers, "make_session", lambda: None)

    def _fake_process(company, url, session, profiles, techmap_index, force):
        return company, 1, 0, False, None

    monkeypatch.setattr(update_jobs, "_process_company", _fake_process)

    stats = update_jobs.scrape_stage({"Acme": "https://acme.com/careers"}, profiles={})

    assert stats.companies_checked == 1


# --- ATS API tier (_ats_api_jobs) ------------------------------------------

def test_ats_api_jobs_returns_none_for_a_non_ats_url():
    assert update_jobs._ats_api_jobs(session=None, url="https://acme.com/careers") is None


def test_ats_api_jobs_returns_none_for_no_url():
    assert update_jobs._ats_api_jobs(session=None, url=None) is None


def test_ats_api_jobs_maps_fields_from_a_resolved_board(monkeypatch):
    monkeypatch.setattr(update_jobs.ats_fetchers, "fetch_company_board", lambda session, ats, token, url: [
        {
            "title": "Backend Engineer", "location": "Tel Aviv", "url": "https://boards.greenhouse.io/acme/1",
            "description": "build things", "department": "Engineering", "employment_type": "Full-time",
            "posted_at": "2026-06-15", "_ats": "greenhouse",
        },
    ])

    jobs = update_jobs._ats_api_jobs(session=None, url="https://boards.greenhouse.io/acme")

    assert jobs == [{
        "title": "Backend Engineer", "location": "Tel Aviv", "url": "https://boards.greenhouse.io/acme/1",
        "description": "build things", "department": "Engineering", "employment_type": "Full-time",
        "posted_at": "2026-06-15",
    }]


def test_ats_api_jobs_returns_empty_list_when_board_resolves_but_has_zero_jobs(monkeypatch):
    monkeypatch.setattr(update_jobs.ats_fetchers, "fetch_company_board", lambda session, ats, token, url: [])

    jobs = update_jobs._ats_api_jobs(session=None, url="https://boards.greenhouse.io/acme")

    assert jobs == []


def test_ats_api_jobs_returns_empty_list_when_the_api_call_raises(monkeypatch):
    def _boom(session, ats, token, url):
        raise RuntimeError("network blew up")
    monkeypatch.setattr(update_jobs.ats_fetchers, "fetch_company_board", _boom)

    jobs = update_jobs._ats_api_jobs(session=None, url="https://boards.greenhouse.io/acme")

    assert jobs == []


def test_fetch_company_jobs_async_uses_the_ats_api_tier_first(monkeypatch):
    async def _run():
        monkeypatch.setattr(update_jobs.ats_fetchers, "fetch_company_board", lambda session, ats, token, url: [
            {"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x", "description": "python required"},
        ])
        called_generic = []
        monkeypatch.setattr(update_jobs.ats_fetchers, "fetch_listing_links", lambda *a, **kw: called_generic.append(1) or [])

        profiles = {"default": {"must_have_keywords": ["python"]}}
        jobs = await update_jobs.fetch_company_jobs_async(
            "Acme", "https://boards.greenhouse.io/acme", session=None, profiles=profiles, techmap_index={},
        )

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Backend Engineer"
        assert called_generic == []  # never fell through to generic HTML scraping

    asyncio.run(_run())


def test_fetch_company_jobs_async_falls_through_when_ats_board_scores_nothing(monkeypatch):
    async def _run():
        monkeypatch.setattr(update_jobs.ats_fetchers, "fetch_company_board", lambda session, ats, token, url: [
            {"title": "Sales Manager", "location": "Tel Aviv", "url": "https://x", "description": "irrelevant"},
        ])
        monkeypatch.setattr(
            update_jobs.ats_fetchers, "fetch_listing_links",
            lambda *a, **kw: [("Backend Engineer", "https://x/2")],
        )
        monkeypatch.setattr(
            update_jobs.ats_fetchers, "fetch_generic_job_details",
            lambda *a, **kw: {
                "description": "python required, " * 5, "location": None, "employment_type": None, "posted_at": None,
            },
        )

        profiles = {"default": {"must_have_keywords": ["python"]}}
        jobs = await update_jobs.fetch_company_jobs_async(
            "Acme", "https://boards.greenhouse.io/acme", session=None, profiles=profiles, techmap_index={},
        )

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Backend Engineer"

    asyncio.run(_run())


# --- _has_real_descriptions -------------------------------------------------

def test_has_real_descriptions_false_for_no_jobs():
    assert update_jobs._has_real_descriptions([]) is False


def test_has_real_descriptions_true_when_most_jobs_have_a_real_description():
    jobs = [
        {"description": "x" * 50},
        {"description": "x" * 50},
        {"description": ""},
    ]
    assert update_jobs._has_real_descriptions(jobs) is True


def test_has_real_descriptions_false_when_descriptions_are_mostly_empty_or_thin():
    """Real case: a title alone can still score positive, but if the
    description is just short site-chrome (or missing outright) it's useless
    for the CV-coverage scoring the description exists to feed."""
    jobs = [
        {"description": ""},
        {"description": "short"},
        {"description": "x" * 50},
    ]
    assert update_jobs._has_real_descriptions(jobs) is False


def test_has_real_descriptions_treats_a_missing_description_key_as_empty():
    assert update_jobs._has_real_descriptions([{"title": "Backend Engineer"}]) is False


# --- description-gated escalation to Playwright -----------------------------

def test_fetch_company_jobs_async_escalates_to_playwright_when_plain_http_titles_score_but_descriptions_are_thin(monkeypatch):
    """Real case caught live: Adaptive6's Webflow careers page yields titles
    that score fine from plain HTTP, but the individual job pages are a JS
    SPA with no description in the static HTML - only site nav. Without this
    gate, the cascade would accept the title-only result and never try
    Playwright, silently shipping a job with no description for CV scoring."""
    async def _run():
        monkeypatch.setattr(update_jobs.ats_fetchers, "resolve_ats", lambda url: None)
        monkeypatch.setattr(
            update_jobs.ats_fetchers, "fetch_listing_links",
            lambda *a, **kw: [("Backend Engineer", "https://x/1")],
        )
        monkeypatch.setattr(
            update_jobs.ats_fetchers, "fetch_generic_job_details",
            lambda *a, **kw: {"description": "", "location": None, "employment_type": None, "posted_at": None},
        )

        async def _fake_playwright(company, url):
            return [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": "x" * 100}]
        monkeypatch.setattr(update_jobs, "_fetch_via_playwright", _fake_playwright)

        profiles = {"default": {"must_have_keywords": ["backend"]}}
        jobs = await update_jobs.fetch_company_jobs_async(
            "Acme", "https://acme.com/careers", session=None, profiles=profiles, techmap_index={},
        )

        assert len(jobs) == 1
        assert jobs[0]["description"] == "x" * 100

    asyncio.run(_run())


def test_fetch_company_jobs_async_keeps_plain_http_result_when_playwright_gets_nothing_better(monkeypatch):
    """When plain HTTP scored (title-only) and Playwright's rescue attempt
    fails or also comes back thin, the cascade should still prefer the
    plain-HTTP result (a title-only match) over techmap's even-thinner
    title/location-only fallback."""
    async def _run():
        monkeypatch.setattr(update_jobs.ats_fetchers, "resolve_ats", lambda url: None)
        monkeypatch.setattr(
            update_jobs.ats_fetchers, "fetch_listing_links",
            lambda *a, **kw: [("Backend Engineer", "https://x/1")],
        )
        monkeypatch.setattr(
            update_jobs.ats_fetchers, "fetch_generic_job_details",
            lambda *a, **kw: {"description": "", "location": None, "employment_type": None, "posted_at": None},
        )

        async def _empty_playwright(company, url):
            return []
        monkeypatch.setattr(update_jobs, "_fetch_via_playwright", _empty_playwright)

        profiles = {"default": {"must_have_keywords": ["backend"]}}
        jobs = await update_jobs.fetch_company_jobs_async(
            "Acme", "https://acme.com/careers", session=None, profiles=profiles, techmap_index={"acme": [
                {"title": "Backend Engineer", "location": "Remote", "url": "https://x/1"},
            ]},
        )

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Backend Engineer"
        assert jobs[0]["description"] == ""

    asyncio.run(_run())
