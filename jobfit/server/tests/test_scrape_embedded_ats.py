"""Embedded-ATS detection (jobfit.scrape.ats.embedded) and the fetchers for
the providers added for it. Everything runs offline against a fake session."""

import json

import pytest

from jobfit import ats_fetchers
from jobfit.scrape import models
from jobfit.scrape.ats import default_registry
from jobfit.scrape.ats.embedded import find_embedded_ats
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.fetchers import PageFetcher, make_page
from jobfit.scrape.strategies import EmbeddedAtsScrape

COMEET_WIDGET_PAGE = """<html><body><div id="comeet-jobs"></div>
<script src="https://www.comeet.co/careers-api/api.js"></script>
<script>COMEET.init({ "token": "89433783C0C22503378337889419BC33782AE4", "company-uid": "98.004", "company-name":"backslash" })</script>
</body></html>"""
GREENHOUSE_EMBED_PAGE = '<html><body><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script></body></html>'
ASHBY_EMBED_PAGE = """<html><body><script src="https://jobs.ashbyhq.com/ashby-job-board-embed.js"></script>
<script>window.__Ashby={"jobBoardName":"acme","url":"https:\\/\\/jobs.ashbyhq.com\\/acme"}</script></body></html>"""
PLAIN_PAGE = '<html><body><a href="/careers/backend">Backend Engineer</a></body></html>'


class _Response:
    def __init__(self, payload=None, status=200, content=b""):
        self._payload, self.status_code, self.content = payload, status, content
        self.text = content.decode("utf-8") if content else json.dumps(payload or {})

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response(url, kwargs) if callable(response) else response
        return _Response(status=404)


def test_find_embedded_ats_reads_comeet_init_config():
    client, board = find_embedded_ats(COMEET_WIDGET_PAGE, default_registry(session=None))
    assert client.provider == "comeet" and board == "98.004:89433783C0C22503378337889419BC33782AE4"


def test_find_embedded_ats_reads_greenhouse_embed_script_and_json_escaped_urls():
    registry = default_registry(session=None)
    client, board = find_embedded_ats(GREENHOUSE_EMBED_PAGE, registry)
    assert (client.provider, board) == ("greenhouse", "acme")
    client, board = find_embedded_ats(ASHBY_EMBED_PAGE, registry)
    assert (client.provider, board) == ("ashby", "acme")  # the embed .js must not win


def test_a_partners_board_linked_from_the_page_is_not_the_companys_board():
    """Mobileye's careers page links "See robotics roles" to Mentee Robotics'
    Comeet board. With a company hint, a link-derived board must share a
    name token with the company; widget credentials on the page itself
    are exempt (they are the page's own)."""
    registry = default_registry(session=None)
    html = '<a href="https://www.comeet.com/jobs/mentee_robotics/6A.002/robotics-engineer/1A.2B3">See robotics roles</a>'
    assert find_embedded_ats(html, registry, company_hint="mobileye careers.mobileye.com") is None
    client, board = find_embedded_ats(html, registry, company_hint="mentee_robotics_ltd www.menteebot.com")
    assert (client.provider, board) == ("comeet", "mentee_robotics/6A.002")
    assert find_embedded_ats(html, registry) is not None  # no hint = no check
    assert find_embedded_ats(COMEET_WIDGET_PAGE, registry, company_hint="unrelated_co example.com") is not None


def test_embedded_candidates_are_ordered_and_a_dead_first_board_does_not_hide_the_live_one(monkeypatch):
    """HoneyBook: an old Greenhouse embed still in the page next to the live
    Ashby board. Candidates come back in order; the scrape tries each."""
    from jobfit.scrape.ats.embedded import find_embedded_ats_candidates

    html = ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=honeybook"></script>'
            '<a href="https://jobs.ashbyhq.com/honeybook/1">Backend</a><a href="https://jobs.ashbyhq.com/honeybook/2">QA</a>')
    registry = default_registry(session=None)
    cands = find_embedded_ats_candidates(html, registry, "honeybook www.honeybook.com")
    assert [(c.provider, b) for c, b in cands] == [("ashby", "honeybook"), ("greenhouse", "honeybook")]  # by frequency

    class _Fetcher(PageFetcher):
        def fetch(self, url):
            return make_page(url, url, 200, html, "http")

    monkeypatch.setattr(ats_fetchers, "fetch_ashby", lambda session, token: None)  # dead now
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: [{"title": "Designer", "url": "https://x/1"}])
    postings = EmbeddedAtsScrape(_Fetcher(), registry).fetch("HoneyBook", "https://www.honeybook.com/careers")
    assert [p.title for p in postings] == ["Designer"]


def test_fetch_eightfold_maps_positions_and_pages():
    def api(url, kwargs):
        start = kwargs["params"]["start"]
        assert kwargs["params"]["domain"] == "tevapharm.com" and kwargs["params"]["location"] == "Israel"
        return _Response({"count": 120, "positions": [
            {"id": str(start + i), "name": f"Role {start + i}", "location": "Shoham, Israel", "department": "Marketing", "t_create": "1788852625",
             "canonicalPositionUrl": f"https://www.careers.teva/careers/job/{start + i}", "job_description": "<p>Lead <b>things</b></p>"}
            for i in range(100 if start == 0 else 20)
        ]})

    session = _Session({"https://www.careers.teva/api/apply/v2/jobs": api})
    jobs = ats_fetchers.fetch_eightfold(session, "www.careers.teva|tevapharm.com")
    assert len(jobs) == 120 and jobs[0]["url"] == "https://www.careers.teva/careers/job/0"
    assert jobs[0]["location"] == "Shoham, Israel" and jobs[0]["department"] == "Marketing" and jobs[0]["posted_at"] == "2026-09-08"
    assert jobs[0]["description"] == "Lead\nthings"


def test_planner_renders_a_zero_yield_http_page_and_takes_what_the_rendered_page_shows():
    from jobfit.server.tests.test_scrape_planner import _planner

    static = '<html><body><p>Join us. We are a great place to work with many benefits and offices worldwide. ' + 'Lorem ipsum ' * 60 + '</p><a href="/about">About Us Page</a></body></html>'
    rendered = """<html><body><ul>
     <li><a href="/careers/backend-engineer-1">Backend Engineer</a></li>
     <li><a href="/careers/frontend-engineer-2">Frontend Engineer</a></li>
     <li><a href="/careers/devops-engineer-3">DevOps Engineer</a></li></ul></body></html>"""
    planner, _ = _planner({("http", "https://acme.com/careers/"): (200, static, None), ("playwright", "https://acme.com/careers/"): (200, rendered, None)})
    plan, page = planner.discover("acme", "https://acme.com/careers/")
    assert plan.strategy.kind == "html_listing" and plan.strategy.renderer == "playwright" and plan.health.baseline_yield == 3
    assert page.renderer == "playwright" and any("rendered page" in n for n in plan.notes)


def test_find_embedded_ats_is_none_for_a_plain_page():
    assert find_embedded_ats(PLAIN_PAGE, default_registry(session=None)) is None
    assert find_embedded_ats("", default_registry(session=None)) is None


def test_embedded_ats_scrape_fetches_the_board_it_finds(monkeypatch):
    class _Fetcher(PageFetcher):
        def fetch(self, url):
            return make_page(url, url, 200, COMEET_WIDGET_PAGE, "http")

    monkeypatch.setattr(ats_fetchers, "fetch_comeet_widget", lambda session, uid, token: [{"title": "Security Researcher", "url": "https://x/1"}])
    postings = EmbeddedAtsScrape(_Fetcher(), default_registry(session=None)).fetch("Backslash", "https://backslash.security/careers")
    assert [p.title for p in postings] == ["Security Researcher"] and postings[0].source == "ats_api"


def test_fetch_comeet_widget_maps_positions_and_tags_israel():
    session = _Session({"https://www.comeet.co/careers-api/2.0/company/B3.006/positions": _Response([
        {"name": "Backend Tech Lead", "department": "R&D", "employment_type": "Full-time", "time_updated": "2026-09-06T07:55:30Z",
         "location": {"name": "Tel Aviv", "country": "IL", "city": "Tel Aviv-Yafo"},
         "url_active_page": "https://corporate.365scores.com/careers/position/?jobid=B5.652",
         "details": [{"name": "Description", "value": "<p>Build <b>APIs</b></p>"}, {"name": "Requirements", "value": "<ul><li>.NET</li></ul>"}]},
        {"name": "Internal only", "is_internal": True, "details": []},
    ])})
    jobs = ats_fetchers.fetch_comeet_widget(session, "B3.006", "TOKEN")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Backend Tech Lead" and job["location"] == "Tel Aviv, Israel" and job["department"] == "R&D"
    assert "Build\nAPIs" in job["description"] and ".NET" in job["description"]
    assert job["url"].endswith("jobid=B5.652") and job["posted_at"] == "2026-09-06"
    assert "details=true" in session.calls[0][1]


def test_fetch_recruitee_breezy_bamboohr_map_their_shapes():
    session = _Session({
        "https://acme.recruitee.com/api/offers/": _Response({"offers": [
            {"title": "Data Engineer", "status": "published", "careers_url": "https://acme.recruitee.com/o/data", "city": "Tel Aviv", "country": "Israel",
             "department": "Data", "employment_type_code": "fulltime", "description": "<p>x</p>", "requirements": "<p>y</p>", "published_at": "2026-09-01T00:00:00Z"},
        ]}),
        "https://acme.breezy.hr/json": _Response([
            {"name": "QA Engineer", "url": "https://acme.breezy.hr/p/1-qa", "location": {"name": "Haifa, Israel"}, "type": {"name": "Full-Time"}, "department": "QA", "description": "<p>q</p>"},
        ]),
        "https://acme.bamboohr.com/careers/list": _Response({"result": [
            {"id": 7, "jobOpeningName": "SRE", "departmentLabel": "Ops", "employmentStatusLabel": "Full-Time", "location": {"city": "Herzliya", "state": None}, "isRemote": False},
        ]}),
    })
    r = ats_fetchers.fetch_recruitee(session, "acme")[0]
    assert (r["title"], r["location"], r["description"]) == ("Data Engineer", "Tel Aviv, Israel", "x\ny")
    b = ats_fetchers.fetch_breezy(session, "acme")[0]
    assert (b["title"], b["location"], b["employment_type"]) == ("QA Engineer", "Haifa, Israel", "Full-Time")
    h = ats_fetchers.fetch_bamboohr(session, "acme")[0]
    assert (h["title"], h["location"], h["url"]) == ("SRE", "Herzliya", "https://acme.bamboohr.com/careers/7")


def test_fetch_smartrecruiters_and_workday_page_through_results():
    def sr(url, kwargs):
        offset = int(url.split("offset=")[1])
        return _Response({"totalFound": 150, "content": [
            {"id": f"j{offset + i}", "name": f"Role {offset + i}", "location": {"city": "Tel Aviv", "country": "il"}} for i in range(100 if offset == 0 else 50)
        ]})

    def wd(url, kwargs):
        offset = kwargs["json"]["offset"]
        assert kwargs["json"]["searchText"] == "Israel"
        return _Response({"total": 25, "jobPostings": [
            {"title": f"Eng {offset + i}", "externalPath": f"/job/Tel-Aviv/Eng_{offset + i}", "locationsText": "Tel Aviv, Israel"} for i in range(20 if offset == 0 else 5)
        ]})

    session = _Session({"https://api.smartrecruiters.com/v1/companies/acme/postings": sr, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/jobs": wd})
    sr_jobs = ats_fetchers.fetch_smartrecruiters(session, "acme")
    assert len(sr_jobs) == 150 and sr_jobs[0]["url"] == "https://jobs.smartrecruiters.com/acme/j0"
    wd_jobs = ats_fetchers.fetch_workday(session, "acme.wd5/Careers")
    assert len(wd_jobs) == 25 and wd_jobs[0]["url"] == "https://acme.wd5.myworkdayjobs.com/Careers/job/Tel-Aviv/Eng_0"


def test_fetch_personio_parses_the_xml_feed():
    xml = b"""<?xml version="1.0"?><workzag-jobs><position><id>11</id><office>Tel Aviv</office><department>Engineering</department>
<name>Platform Engineer</name><employmentType>permanent</employmentType>
<jobDescriptions><jobDescription><name>Role</name><value><![CDATA[<p>Run the <b>platform</b></p>]]></value></jobDescription></jobDescriptions>
</position></workzag-jobs>"""
    session = _Session({"https://acme.jobs.personio.de/xml": _Response(content=xml)})
    job = ats_fetchers.fetch_personio(session, "acme")[0]
    assert (job["title"], job["location"], job["url"]) == ("Platform Engineer", "Tel Aviv", "https://acme.jobs.personio.de/job/11")
    assert "Run the\nplatform" in job["description"]


LANDING_PAGE = """<html><body>
<nav><a href="/about">About Us Page</a><a href="/platform">Platform Overview</a></nav>
<h1>Careers</h1><p>We are hiring.</p>
<a href="/careers/jobs">See all open roles</a>
<a href="/careers/benefits">Benefits Overview</a>
</body></html>"""
HOP_PAGE = """<html><body><ul>
 <li><a href="/careers/jobs/backend-engineer-1">Backend Engineer</a></li>
 <li><a href="/careers/jobs/frontend-engineer-2">Frontend Engineer</a></li>
 <li><a href="/careers/jobs/devops-engineer-3">DevOps Engineer</a></li>
</ul></body></html>"""


def test_planner_follows_a_landing_page_to_the_listing_one_hop_away():
    from jobfit.server.tests.test_scrape_planner import _planner

    planner, _ = _planner({
        ("http", "https://acme.com/careers/"): (200, LANDING_PAGE, None),
        ("http", "https://acme.com/careers/jobs"): (200, HOP_PAGE, None),
    })
    plan, page = planner.discover("acme", "https://acme.com/careers/")
    assert plan.strategy.kind == "html_listing" and plan.strategy.listing_url == "https://acme.com/careers/jobs"
    assert plan.career_url == "https://acme.com/careers/" and plan.health.baseline_yield == 3
    assert page.url == "https://acme.com/careers/jobs"  # the snapshot is of the listing page
    assert any("one hop away" in n for n in plan.notes)


def test_planner_hop_landing_on_an_embedded_ats_yields_an_ats_plan():
    from jobfit.server.tests.test_scrape_planner import _planner

    planner, _ = _planner({
        ("http", "https://acme.com/careers/"): (200, LANDING_PAGE, None),
        ("http", "https://acme.com/careers/jobs"): (200, COMEET_WIDGET_PAGE, None),
    })
    plan, _ = planner.discover("acme", "https://acme.com/careers/")
    assert isinstance(plan.strategy, models.AtsApiStrategy) and plan.strategy.provider == "comeet"


def test_html_listing_scrape_fetches_the_listing_url_when_set():
    from jobfit.scrape.classifiers import rules_chain
    from jobfit.scrape.strategies import HtmlListingScrape
    from jobfit.scrape.enrich import NoopEnricher

    class _Fetcher(PageFetcher):
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            return make_page(url, url, 200, HOP_PAGE, "http")

    strategy = models.HtmlListingStrategy(listing_url="https://acme.com/careers/jobs", explicit_accept=[
        "https://acme.com/careers/jobs/backend-engineer-1"])
    fetcher = _Fetcher()
    scrape = HtmlListingScrape(fetcher, CandidateExtractor(), rules_chain(), NoopEnricher(), strategy)
    postings = scrape.fetch("Acme", "https://acme.com/careers/")
    assert fetcher.calls == ["https://acme.com/careers/jobs"]
    assert {p.title for p in postings} >= {"Backend Engineer"}


@pytest.mark.parametrize("html", [COMEET_WIDGET_PAGE, GREENHOUSE_EMBED_PAGE])
def test_planner_turns_an_embedded_ats_into_an_ats_api_plan_without_classifying(html):
    from jobfit.server.tests.test_scrape_planner import _planner

    planner, classifier = _planner({("http", "https://acme.com/careers/"): (200, html, None)})
    plan, _ = planner.discover("acme", "https://acme.com/careers/")
    assert isinstance(plan.strategy, models.AtsApiStrategy) and plan.status == "verified" and plan.derived_by == "probe"
    assert classifier.calls == 0
