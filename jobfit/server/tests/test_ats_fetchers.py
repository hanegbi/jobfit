"""ats_fetchers.py's pure logic (URL -> ATS token resolution, HTML cleaning,
boilerplate detection) had zero test coverage despite being what decides
which of the 5 API fetchers gets used for every company, and what turns raw
scraped HTML into what actually gets stored as a job's description."""

from jobfit import ats_fetchers


# --- resolve_ats -----------------------------------------------------------

def test_resolve_ats_greenhouse():
    assert ats_fetchers.resolve_ats("https://boards.greenhouse.io/acme/jobs/12345") == ("greenhouse", "acme")


def test_resolve_ats_lever():
    assert ats_fetchers.resolve_ats("https://jobs.lever.co/acme/abc123-def456") == ("lever", "acme")


def test_resolve_ats_ashby():
    assert ats_fetchers.resolve_ats("https://jobs.ashbyhq.com/acme/abcdef") == ("ashby", "acme")


def test_resolve_ats_comeet():
    assert ats_fetchers.resolve_ats("https://www.comeet.com/jobs/acme/12.345/some-job/67.890") == ("comeet", "acme")


def test_resolve_ats_workable():
    assert ats_fetchers.resolve_ats("https://apply.workable.com/acme/j/ABCDEF1234/") == ("workable", "acme")


def test_resolve_ats_returns_none_for_an_unknown_host():
    assert ats_fetchers.resolve_ats("https://acme.com/careers/backend-engineer") is None


def test_resolve_ats_returns_none_for_no_url():
    assert ats_fetchers.resolve_ats(None) is None
    assert ats_fetchers.resolve_ats("") is None


# --- strip_html --------------------------------------------------------

def test_strip_html_removes_tags():
    assert ats_fetchers.strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_unescapes_entities_before_stripping():
    assert ats_fetchers.strip_html("&lt;div&gt;Hello&lt;/div&gt;") == "Hello"


def test_strip_html_returns_empty_for_none_or_empty():
    assert ats_fetchers.strip_html(None) == ""
    assert ats_fetchers.strip_html("") == ""


def test_strip_html_collapses_whitespace_in_plain_text():
    assert ats_fetchers.strip_html("Hello   world\n\ntab\there") == "Hello world tab here"


# --- looks_like_boilerplate ----------------------------------------------

def test_looks_like_boilerplate_flags_a_short_cookie_notice():
    assert ats_fetchers.looks_like_boilerplate("We use cookies. Manage Consent below.") is True


def test_looks_like_boilerplate_does_not_flag_a_real_short_description():
    assert ats_fetchers.looks_like_boilerplate("We are hiring a backend engineer with Python experience.") is False


def test_looks_like_boilerplate_never_flags_long_text_even_with_a_marker():
    long_text = ("We are hiring a great engineer. " * 60) + "Manage Consent"
    assert len(long_text) >= ats_fetchers._SHORT_BOILERPLATE_LEN
    assert ats_fetchers.looks_like_boilerplate(long_text) is False


# --- JSON-LD JobPosting extraction ------------------------------------------

def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def test_jsonld_job_postings_finds_a_single_object():
    html = '<script type="application/ld+json">{"@type": "JobPosting", "title": "Backend Engineer"}</script>'
    postings = ats_fetchers._jsonld_job_postings(_soup(html))
    assert len(postings) == 1
    assert postings[0]["title"] == "Backend Engineer"


def test_jsonld_job_postings_finds_objects_in_a_list():
    html = '<script type="application/ld+json">[{"@type": "JobPosting", "title": "A"}, {"@type": "Organization"}]</script>'
    postings = ats_fetchers._jsonld_job_postings(_soup(html))
    assert len(postings) == 1
    assert postings[0]["title"] == "A"


def test_jsonld_job_postings_unwraps_a_graph_wrapper():
    html = '<script type="application/ld+json">{"@graph": [{"@type": "JobPosting", "title": "A"}, {"@type": "WebPage"}]}</script>'
    postings = ats_fetchers._jsonld_job_postings(_soup(html))
    assert len(postings) == 1
    assert postings[0]["title"] == "A"


def test_jsonld_job_postings_ignores_malformed_json():
    html = '<script type="application/ld+json">{not valid json</script>'
    assert ats_fetchers._jsonld_job_postings(_soup(html)) == []


def test_jsonld_job_postings_returns_empty_when_no_script_tags():
    assert ats_fetchers._jsonld_job_postings(_soup("<html><body>hi</body></html>")) == []


def test_jsonld_location_extracts_from_nested_postal_address():
    posting = {"jobLocation": {"address": {"addressLocality": "Tel Aviv", "addressRegion": "", "addressCountry": "IL"}}}
    assert ats_fetchers._jsonld_location(posting) == "Tel Aviv, IL"


def test_jsonld_location_handles_addressCountry_as_a_nested_object():
    """Real crash found live on A2Z Cust2Mate's job pages: addressCountry was
    {"@type": "Country", "name": "IL"} instead of a plain string, and the
    original ", ".join(...) raised TypeError on the dict."""
    posting = {"jobLocation": {"address": {
        "addressLocality": "Giv'atayim", "addressRegion": "Tel Aviv District",
        "addressCountry": {"@type": "Country", "name": "IL"},
    }}}
    assert ats_fetchers._jsonld_location(posting) == "Giv'atayim, Tel Aviv District, IL"


def test_jsonld_location_handles_a_plain_string_address():
    posting = {"jobLocation": {"address": "Remote"}}
    assert ats_fetchers._jsonld_location(posting) == "Remote"


def test_jsonld_location_handles_a_list_of_locations_using_the_first():
    posting = {"jobLocation": [{"address": {"addressLocality": "Haifa"}}, {"address": {"addressLocality": "Tel Aviv"}}]}
    assert ats_fetchers._jsonld_location(posting) == "Haifa"


def test_jsonld_location_returns_none_when_absent():
    assert ats_fetchers._jsonld_location({}) is None


def test_jsonld_location_returns_none_when_fields_are_empty():
    posting = {"jobLocation": {"address": {"addressLocality": "", "addressRegion": "", "addressCountry": ""}}}
    assert ats_fetchers._jsonld_location(posting) is None


def test_fetch_generic_job_details_returns_empty_for_a_skipped_host():
    result = ats_fetchers.fetch_generic_job_details(session=None, url="https://www.linkedin.com/jobs/view/123")
    assert result == {"description": "", "location": None, "employment_type": None, "posted_at": None}


def test_fetch_generic_job_details_returns_empty_for_no_url():
    result = ats_fetchers.fetch_generic_job_details(session=None, url=None)
    assert result == {"description": "", "location": None, "employment_type": None, "posted_at": None}


# --- title extraction & boilerplate stripping -------------------------------

def test_link_title_text_prefers_a_nested_heading_over_the_whole_anchor():
    """Real example caught live: Adaptive6's Webflow careers page wraps an
    entire job card - title, department tag, location, description snippet,
    an "Apply Now" CTA - in one <a>, so a.get_text() produced "Senior Backend
    Developer Engineering Israel Apply Now" instead of just the real title."""
    html = (
        '<a href="/x"><h2>Senior Backend Developer</h2>'
        '<div class="tag">Engineering</div><div class="location">Israel</div>'
        '<span>Apply Now</span></a>'
    )
    a = _soup(html).find("a")
    assert ats_fetchers._link_title_text(a) == "Senior Backend Developer"


def test_link_title_text_falls_back_to_full_text_when_no_heading():
    html = '<a href="/x">Backend Engineer</a>'
    a = _soup(html).find("a")
    assert ats_fetchers._link_title_text(a) == "Backend Engineer"


def test_strip_boilerplate_keeps_a_header_that_contains_a_real_job_link():
    """Real bug caught live: the entire job-listing section on Adaptive6's
    careers page is wrapped in <header class="section_careers"> (a loose,
    non-standard use of <header> as "this section's heading area", not
    site navigation) - the old blanket decompose() on every <header> in the
    document silently wiped every job listing before any title-cleaning
    logic even ran."""
    soup = _soup(
        '<header class="section_careers"><a href="/x"><h2>Senior Backend Developer</h2></a></header>'
    )
    ats_fetchers._strip_boilerplate(soup)
    assert soup.find("header") is not None
    assert soup.find("a") is not None


def test_strip_boilerplate_removes_a_header_with_no_job_link():
    soup = _soup('<header><a href="/">Home</a><a href="/about">About</a></header><p>Body text</p>')
    ats_fetchers._strip_boilerplate(soup)
    assert soup.find("header") is None
    assert soup.find("p") is not None


def test_strip_boilerplate_removes_nav_and_footer_without_job_links():
    soup = _soup('<nav><a href="/">Home</a></nav><p>Body</p><footer><a href="/terms">Terms</a></footer>')
    ats_fetchers._strip_boilerplate(soup)
    assert soup.find("nav") is None
    assert soup.find("footer") is None
    assert soup.find("p") is not None


def test_strip_boilerplate_still_removes_script_style_svg_form_noscript():
    soup = _soup(
        "<script>alert(1)</script><style>.a{}</style><svg></svg><form></form><noscript>x</noscript><p>Body</p>"
    )
    ats_fetchers._strip_boilerplate(soup)
    assert soup.find(["script", "style", "svg", "form", "noscript"]) is None
    assert soup.find("p") is not None


# --- fetch_elbit_sigmabit_jobs -----------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_elbit_sigmabit_jobs_maps_fields(monkeypatch):
    """Real structure returned by elbitsystemscareer.com/cron/jobs.json - the
    site the DOM scraper can never see job links on, since every job card
    is a JS-driven div with no <a href> at all."""
    payload = [
        {
            "jobId": 20234, "jobTitle": 'מחסנאי.ת תחמושת פצמ"ר', "status": 1,
            "description": "&lt;div&gt;לאתר החברה ביקנעם&lt;/div&gt;",
            "area": "North", "employmentType": None, "openDate": "2026-03-22T03:46:00",
        },
    ]
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: _FakeResponse(payload))

    jobs = ats_fetchers.fetch_elbit_sigmabit_jobs(session=None)

    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == 'מחסנאי.ת תחמושת פצמ"ר'
    assert job["location"] == "North"
    assert job["url"] == "https://elbitsystemscareer.com/jobs/?id=20234"
    assert job["description"] == "לאתר החברה ביקנעם"
    assert job["posted_at"] == "2026-03-22"


def test_fetch_elbit_sigmabit_jobs_skips_non_open_status(monkeypatch):
    payload = [{"jobId": 1, "jobTitle": "Closed Role", "status": 0, "description": "", "area": None, "openDate": None}]
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: _FakeResponse(payload))

    assert ats_fetchers.fetch_elbit_sigmabit_jobs(session=None) == []


def test_fetch_elbit_sigmabit_jobs_returns_empty_when_the_request_fails(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: None)

    assert ats_fetchers.fetch_elbit_sigmabit_jobs(session=None) == []
