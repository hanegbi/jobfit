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
