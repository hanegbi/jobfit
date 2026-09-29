"""Jobs inlined as page JSON (Next.js/Nuxt career SPAs with no per-job links)."""

import json

from jobfit.scrape import models
from jobfit.scrape.fetchers import PageFetcher, make_page
from jobfit.scrape.inline_json import find_inline_jobs
from jobfit.scrape.strategies import InlineJsonScrape

NEXT_DATA = {"props": {"pageProps": {
    "allRecipes": [
        {"id": "1", "title": "Senior Backend Engineer - Platform", "department": "Tech Development", "link": "https://makers.lemonade.com/role/senior-backend", "location": "Tel Aviv, Israel", "employmentType": "Full-time"},
        {"id": "2", "title": "Product Designer", "department": "Design", "link": "/role/product-designer", "location": "Tel Aviv, Israel", "employmentType": "Full-time"},
        {"id": "3", "title": "Claims Advocate", "department": "Claims", "link": "https://makers.lemonade.com/role/claims", "location": "Remote, United States", "employmentType": "Full-time"},
    ],
    "cover_images": [
        {"id": "221", "title": "Lemonade Office - NYC", "url": "https://s3.amazonaws.com/x/cover_1.jpg", "alt": "office"},
        {"id": "222", "title": "Lemonade Office - TLV", "url": "https://s3.amazonaws.com/x/cover_2.jpg", "alt": "office"},
    ],
    "blogPosts": [
        {"title": "Why we love Tel Aviv", "url": "https://blog.example.com/tlv", "category": "Culture"},
        {"title": "Our new office", "url": "https://blog.example.com/office", "category": "Culture"},
    ],
}}}
PAGE = f'<html><body><table><tr><td>Senior Backend Engineer - Platform</td></tr></table><script id="__NEXT_DATA__" type="application/json">{json.dumps(NEXT_DATA)}</script></body></html>'
NUXT_PAGE = '<html><body><script>window.__NUXT__={"data":[{"positions":[{"name":"QA Engineer","href":"/jobs/qa","city":"Haifa"},{"name":"DevOps","href":"/jobs/devops","city":"Haifa"}]}]};</script></body></html>'


def test_find_inline_jobs_reads_next_data_and_skips_images_and_offsite_lists():
    jobs = find_inline_jobs(PAGE, "https://makers.lemonade.com/")
    assert [j["title"] for j in jobs] == ["Senior Backend Engineer - Platform", "Product Designer", "Claims Advocate"]
    assert jobs[1]["url"] == "https://makers.lemonade.com/role/product-designer"  # relative link resolved
    assert jobs[0]["location"] == "Tel Aviv, Israel" and jobs[0]["department"] == "Tech Development" and jobs[0]["employment_type"] == "Full-time"


def test_find_inline_jobs_reads_nuxt_state():
    jobs = find_inline_jobs(NUXT_PAGE, "https://acme.com/careers")
    assert {j["title"] for j in jobs} == {"QA Engineer", "DevOps"} and jobs[0]["location"] == "Haifa"


def test_find_inline_jobs_is_empty_on_plain_pages():
    assert find_inline_jobs('<html><body><a href="/careers/x">Backend</a></body></html>', "https://acme.com/careers") == []
    assert find_inline_jobs("", "https://acme.com/careers") == []


def test_inline_json_scrape_yields_postings():
    class _Fetcher(PageFetcher):
        def fetch(self, url):
            return make_page(url, url, 200, PAGE, "http")

    postings = InlineJsonScrape(_Fetcher()).fetch("Lemonade", "https://makers.lemonade.com/")
    assert len(postings) == 3 and postings[0].source == "html_listing" and postings[0].location == "Tel Aviv, Israel"


def test_planner_marks_a_page_with_inlined_jobs_as_yielding():
    from jobfit.server.tests.test_scrape_planner import _planner

    # The fixture page is tiny, so it reads as a JS shell and gets re-rendered; serve the same page for both renderers.
    planner, _ = _planner({("http", "https://makers.lemonade.com/"): (200, PAGE, None), ("playwright", "https://makers.lemonade.com/"): (200, PAGE, None)})
    plan, _ = planner.discover("lemonade", "https://makers.lemonade.com/")
    assert plan.strategy.kind == "html_listing" and plan.status == "verified" and plan.health.baseline_yield == 3
    assert any("inlined as page JSON" in n for n in plan.notes)


def test_factory_puts_inline_json_right_after_the_link_scrapes():
    from jobfit.server.tests.test_scrape_factory import _factory, _plan

    built = _factory().build(_plan(models.HtmlListingStrategy(renderer="http", fallbacks=["playwright", "techmap"])))
    assert [type(f).__name__ for f in built.fallbacks] == ["HtmlListingScrape", "InlineJsonScrape", "EmbeddedAtsScrape", "TechmapScrape"]
