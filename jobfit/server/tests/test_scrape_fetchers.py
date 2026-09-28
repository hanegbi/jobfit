"""PageFetcher implementations. The HTTP fetcher is exercised with a fake
requests session; Playwright is never launched in tests (the factory is
asked for it with playwright_available=False to prove the fallback)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from jobfit.scrape import errors, fetchers
from jobfit.scrape.models import Page


class _Resp:
    def __init__(self, status, text, url):
        self.status_code = status
        self.text = text
        self.url = url


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if self.error:
            raise self.error
        return self.response


def test_http_fetcher_returns_a_page_with_status_and_visible_text():
    html = "<html><head><script>x()</script><style>a{}</style></head><body><nav>Menu</nav><p>Hello  world</p></body></html>"
    session = _Session(_Resp(200, html, "https://acme.com/careers/"))
    page = fetchers.HttpPageFetcher(session).fetch("https://acme.com/careers")
    assert isinstance(page, Page)
    assert page.status == 200
    assert page.url == "https://acme.com/careers/"
    assert page.requested_url == "https://acme.com/careers"
    assert page.text == "Menu Hello world"
    assert page.renderer == "http"
    assert page.is_js_shell is False


def test_http_fetcher_returns_404_pages_rather_than_raising():
    session = _Session(_Resp(404, "<html><body>gone</body></html>", "https://acme.com/careers"))
    page = fetchers.HttpPageFetcher(session).fetch("https://acme.com/careers")
    assert page.status == 404


def test_http_fetcher_raises_fetch_failed_on_network_error():
    session = _Session(error=requests.ConnectionError("boom"))
    with pytest.raises(errors.FetchFailed):
        fetchers.HttpPageFetcher(session).fetch("https://acme.com/careers")


def test_js_shell_detection():
    shell = '<html><body><div id="root"></div><script>window.__NEXT_DATA__={}</script></body></html>'
    assert fetchers.looks_like_js_shell(shell, fetchers.visible_text(shell)) is True
    real = "<html><body><main>" + "<p>Backend Engineer - Tel Aviv</p>" * 40 + "</main></body></html>"
    assert fetchers.looks_like_js_shell(real, fetchers.visible_text(real)) is False


def test_cached_fetcher_serves_a_fresh_entry_without_calling_inner(tmp_path):
    session = _Session(_Resp(200, "<p>one</p>", "https://acme.com/j/1"))
    inner = fetchers.HttpPageFetcher(session)
    cached = fetchers.CachedPageFetcher(inner, tmp_path, ttl_hours=1)
    first = cached.fetch("https://acme.com/j/1")
    second = cached.fetch("https://acme.com/j/1")
    assert first.html == second.html == "<p>one</p>"
    assert len(session.calls) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_cached_fetcher_refetches_an_expired_entry(tmp_path):
    session = _Session(_Resp(200, "<p>one</p>", "https://acme.com/j/1"))
    cached = fetchers.CachedPageFetcher(fetchers.HttpPageFetcher(session), tmp_path, ttl_hours=1)
    cached.fetch("https://acme.com/j/1")
    entry_path = next(tmp_path.glob("*.json"))
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    entry["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    entry_path.write_text(json.dumps(entry), encoding="utf-8")
    cached.fetch("https://acme.com/j/1")
    assert len(session.calls) == 2


def test_cached_fetcher_does_not_cache_failures(tmp_path):
    session = _Session(error=requests.Timeout("slow"))
    cached = fetchers.CachedPageFetcher(fetchers.HttpPageFetcher(session), tmp_path, ttl_hours=1)
    with pytest.raises(errors.FetchFailed):
        cached.fetch("https://acme.com/j/1")
    assert list(tmp_path.glob("*.json")) == []


def test_factory_builds_http_and_falls_back_to_http_when_playwright_is_unavailable():
    factory = fetchers.PageFetcherFactory(session=_Session(), playwright_available=False)
    assert isinstance(factory.build("http"), fetchers.HttpPageFetcher)
    assert isinstance(factory.build("playwright"), fetchers.HttpPageFetcher)


def test_factory_builds_a_playwright_fetcher_when_available():
    factory = fetchers.PageFetcherFactory(session=_Session(), playwright_available=True)
    assert isinstance(factory.build("playwright"), fetchers.PlaywrightPageFetcher)
