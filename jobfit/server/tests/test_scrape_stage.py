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
