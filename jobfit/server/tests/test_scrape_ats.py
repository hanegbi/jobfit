"""AtsClient per provider (each owns its URL patterns - the old
TOKEN_PATTERNS list split by provider) and the registry that resolves a
URL to (client, board)."""

import pytest

from jobfit import ats_fetchers
from jobfit.scrape import errors
from jobfit.scrape.ats import AtsRegistry, default_registry, to_posting
from jobfit.scrape.ats.clients import ComeetClient, GreenhouseClient


@pytest.mark.parametrize("url, provider, board", [
    ("https://boards.greenhouse.io/acme/jobs/12345", "greenhouse", "acme"),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme"),
    ("https://jobs.lever.co/acme/abc123-def456", "lever", "acme"),
    ("https://jobs.ashbyhq.com/acme/abcdef", "ashby", "acme"),
    ("https://www.comeet.com/jobs/acme/12.345/some-job/67.890", "comeet", "acme/12.345"),
    ("https://www.comeet.com/jobs/acme", "comeet", "acme"),
    ("https://apply.workable.com/acme/j/ABCDEF1234/", "workable", "acme"),
    ("https://acme.workable.com", "workable", "acme"),
    ("https://boards.greenhouse.io/embed/job_board/js?for=acme", "greenhouse", "acme"),
    ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse", "acme"),
    ("https://www.comeet.co/careers-api/2.0/company/B3.006/positions/?token=3B6128EB2219FA019FA19FAB222166B22&details=true", "comeet", "B3.006:3B6128EB2219FA019FA19FAB222166B22"),
    ("https://acme.recruitee.com/o/backend-engineer", "recruitee", "acme"),
    ("https://acme.bamboohr.com/careers/42", "bamboohr", "acme"),
    ("https://acme.breezy.hr/p/abc-backend", "breezy", "acme"),
    ("https://jobs.smartrecruiters.com/Acme/743999", "smartrecruiters", "Acme"),
    ("https://acme.jobs.personio.de/job/123", "personio", "acme"),
    ("https://motorolasolutions.wd5.myworkdayjobs.com/en-US/Careers/job/Tel-Aviv/Engineer_R123", "workday", "motorolasolutions.wd5/Careers"),
])
def test_registry_resolves_known_board_urls(url, provider, board):
    client, token = default_registry(session=None).resolve(url)
    assert (client.provider, token) == (provider, board)


def test_embed_script_hosts_do_not_resolve_to_bogus_boards():
    registry = default_registry(session=None)
    assert registry.resolve("https://jobs.ashbyhq.com/ashby-job-board-embed.js") is None
    assert registry.resolve("https://boards.greenhouse.io/embed/job_board/js") is None
    assert registry.resolve("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/jobs") is None


def test_comeet_widget_board_fetches_the_widget_api(monkeypatch):
    seen = {}
    monkeypatch.setattr(ats_fetchers, "fetch_comeet_widget", lambda session, uid, token: seen.update(uid=uid, token=token) or [
        {"title": "Backend Tech Lead", "url": "https://corporate.365scores.com/careers/position/?jobid=B5.652"},
    ])
    client = ComeetClient(session=None)
    assert client.board_url("B3.006:ABC") == "https://www.comeet.co/careers-api/2.0/company/B3.006/positions?token=ABC"
    assert [p.title for p in client.fetch_board("B3.006:ABC")] == ["Backend Tech Lead"]
    assert seen == {"uid": "B3.006", "token": "ABC"}


def test_registry_returns_none_for_unknown_or_empty_urls():
    registry = default_registry(session=None)
    assert registry.resolve("https://acme.com/careers/backend-engineer") is None
    assert registry.resolve(None) is None
    assert registry.resolve("") is None


def test_registry_client_lookup_and_unknown_provider():
    registry = default_registry(session=None)
    assert registry.client("lever").provider == "lever"
    assert registry.providers() == [
        "greenhouse", "lever", "ashby", "workable", "comeet",
        "recruitee", "bamboohr", "breezy", "smartrecruiters", "personio", "workday",
    ]
    with pytest.raises(errors.PlanInvalid):
        registry.client("taleo")


def test_board_urls():
    registry = default_registry(session=None)
    assert registry.client("greenhouse").board_url("acme") == "https://boards.greenhouse.io/acme"
    assert registry.client("lever").board_url("acme") == "https://jobs.lever.co/acme"
    assert registry.client("ashby").board_url("acme") == "https://jobs.ashbyhq.com/acme"
    assert registry.client("workable").board_url("acme") == "https://apply.workable.com/acme/"
    assert registry.client("comeet").board_url("acme") == "https://www.comeet.com/jobs/acme"


def test_to_posting_maps_fields_and_skips_untitled_items():
    posting = to_posting({
        "title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": "build",
        "department": "Eng", "employment_type": "Full-time", "posted_at": "2026-06-15", "_ats": "greenhouse",
    }, "ats_api")
    assert posting.title == "Backend Engineer" and posting.source == "ats_api" and posting.department == "Eng"
    assert to_posting({"title": "", "url": "https://x"}, "ats_api") is None


def test_fetch_board_wraps_the_provider_fetcher(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: [
        {"title": "Backend Engineer", "url": "https://boards.greenhouse.io/acme/jobs/1", "description": "x"},
        {"title": "", "url": None},
    ])
    postings = GreenhouseClient(session=None).fetch_board("acme")
    assert [p.title for p in postings] == ["Backend Engineer"]
    assert postings[0].source == "ats_api"


def test_fetch_board_raises_fetch_failed_when_the_provider_returns_none(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: None)
    with pytest.raises(errors.FetchFailed):
        GreenhouseClient(session=None).fetch_board("acme")


def test_comeet_falls_back_to_the_hosted_page_when_the_api_is_empty(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_comeet", lambda session, token: [])
    monkeypatch.setattr(ats_fetchers, "fetch_comeet_hosted_page", lambda session, url: [
        {"title": "Data Engineer", "url": "https://www.comeet.com/jobs/acme/87.00D/data/1"},
    ])
    postings = ComeetClient(session=None).fetch_board("acme", known_url="https://www.comeet.com/jobs/acme/87.00D/some-job/2")
    assert [p.title for p in postings] == ["Data Engineer"]


def test_comeet_slug_uid_board_falls_back_to_its_own_hosted_board_page(monkeypatch):
    fetched = []
    monkeypatch.setattr(ats_fetchers, "fetch_comeet", lambda session, token: None)
    monkeypatch.setattr(ats_fetchers, "fetch_comeet_hosted_page", lambda session, url: fetched.append(url) or [{"title": "AI Engineer", "url": "https://x/1"}])
    postings = ComeetClient(session=None).fetch_board("abovesecurity/FA.00D", known_url="https://www.above.security/careers")
    assert [p.title for p in postings] == ["AI Engineer"]
    assert fetched == ["https://www.comeet.com/jobs/abovesecurity/FA.00D"]


def test_fetch_board_can_tag_postings_as_external_board(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_lever", lambda session, token: [{"title": "QA Engineer", "url": "https://jobs.lever.co/acme/1"}])
    postings = default_registry(session=None).client("lever").fetch_board("acme", source="external_board")
    assert postings[0].source == "external_board"
