"""The two legacy listing entry points (plain-HTTP fetch_listing_links and
the Playwright extract_job_links) now delegate to CandidateExtractor +
legacy_listing_chain, so they agree by construction. These tests pin the
behaviour the old listing_heuristics.py tests pinned."""

import asyncio

from jobfit import ats_fetchers
from jobfit.scrape import filters
from jobfit.scripts import playwright_listings


class _FakeHtmlResponse:
    def __init__(self, text, url="https://acme.com/careers/"):
        self.text = text
        self.url = url
        self.status_code = 200


def test_legacy_chain_accepts_everything_not_explicitly_rejected():
    names = [f.name for f in filters.legacy_listing_chain().filters]
    assert names == ["denylist", "cta_label", "href_marker", "category_prefix", "evidence"]
    evidence = filters.legacy_listing_chain().filters[-1]
    assert evidence.min_signals == 0 and evidence.reject_chrome is False


def test_fetch_listing_links_rejects_docs_maps_and_category_overviews_and_keeps_real_jobs(monkeypatch):
    """Behaviour-preserving: the nav's "About Us Page" is still accepted
    here, exactly as before (the old _strip_boilerplate kept a <nav> that
    held any job-looking link, and this one does). Group B's evidence
    chain is what finally rejects nav links; this task only pins today's
    behaviour under the new machinery."""
    html = """
    <nav><a href="/about">About Us Page</a><a href="https://coralogix.com/docs/opentelemetry/">OpenTelemetry</a></nav>
    <a href="/careers/engineering/all">Engineering Roles</a>
    <a href="/careers/engineering/123/backend-engineer/all">Backend Engineer</a>
    <a href="https://www.google.com/maps/place/HaMasger+St+35">HaMasger St 35, Tel Aviv</a>
    """
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: _FakeHtmlResponse(html))
    links = ats_fetchers.fetch_listing_links(session=None, url="https://acme.com/careers/", max_links=8)
    assert links == [
        ("About Us Page", "https://acme.com/about"),
        ("Backend Engineer", "https://acme.com/careers/engineering/123/backend-engineer/all"),
    ]


def test_fetch_listing_links_keeps_flat_query_string_jobs(monkeypatch):
    html = """
    <a href="index.php?a=show&joborderid=1">Administrative Assistant</a>
    <a href="index.php?a=show&joborderid=2">Backend Developer</a>
    """
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: _FakeHtmlResponse(html, "https://careers.checkpoint.com/index.php"))
    links = ats_fetchers.fetch_listing_links(session=None, url="https://careers.checkpoint.com/index.php?q=", max_links=8)
    assert [t for t, _ in links] == ["Administrative Assistant", "Backend Developer"]


def test_contains_a_job_link_uses_the_denylist_text_rule():
    from bs4 import BeautifulSoup
    nav = BeautifulSoup('<nav><a href="/x">Learn More</a></nav>', "html.parser").nav
    assert ats_fetchers._contains_a_job_link(nav) is False
    section = BeautifulSoup('<header><a href="/x">Senior Backend Developer</a></header>', "html.parser").header
    assert ats_fetchers._contains_a_job_link(section) is True


class _FakePwPage:
    def __init__(self, html, url):
        self._html = html
        self.url = url

    async def content(self):
        return self._html


def test_playwright_extract_job_links_uses_the_same_chain():
    html = """
    <footer><a href="/blog/how-we-scaled">How We Scaled</a></footer>
    <a href="/careers/one"><h2>Senior Backend Developer</h2><span>Engineering Israel Apply Now</span></a>
    """
    page = _FakePwPage(html, "https://acme.com/careers/")
    links = asyncio.run(playwright_listings.extract_job_links(page, "https://acme.com/careers/"))
    assert links == [("Senior Backend Developer", "https://acme.com/careers/one")]
