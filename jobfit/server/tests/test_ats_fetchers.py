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
