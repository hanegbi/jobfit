"""CandidateExtractor turns a Page into the feature table every filter,
the rules classifier, and the LLM prompt all read from. It is the only
place those features are computed."""

from datetime import datetime, timezone

from jobfit.scrape.candidates import CandidateExtractor, href_shape, link_title_text
from jobfit.scrape.fetchers import make_page

CAREER_URL = "https://acme.com/careers/"


def _page(html, url=CAREER_URL):
    return make_page(url, url, 200, html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


def test_href_shape_uses_host_parent_segments_and_depth():
    assert href_shape("https://www.acme.com/careers/backend-engineer") == "acme.com|careers|2"
    assert href_shape("https://acme.com/careers/eng/123/backend/all") == "acme.com|careers/eng/123/backend|5"
    assert href_shape("https://acme.com/") == "acme.com||0"


def test_href_shape_for_a_flat_query_string_scheme_uses_the_sorted_query_keys():
    assert href_shape("https://careers.checkpoint.com/index.php?a=show&joborderid=1") == "careers.checkpoint.com|?a,joborderid"


def test_extracts_features_for_each_anchor():
    html = """
    <html><body>
      <nav><a href="/about">About Us Page</a></nav>
      <main><ul>
        <li><a href="/careers/backend-engineer-123">Senior Backend Engineer</a></li>
        <li><a href="/careers/frontend-engineer-124">Frontend Engineer</a></li>
        <li><a href="/careers/devops-engineer-125">DevOps Engineer</a></li>
      </ul></main>
      <footer><a href="https://docs.acme.com/x">Code Governance and Compliance</a></footer>
    </body></html>
    """
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL)
    by_text = {c.text: c for c in candidates}
    assert [c.index for c in candidates] == [0, 1, 2, 3, 4]

    backend = by_text["Senior Backend Engineer"]
    assert backend.href == "https://acme.com/careers/backend-engineer-123"
    assert backend.same_host is True
    assert backend.under_career_path is True
    assert backend.has_job_url_hint is True
    assert backend.in_chrome is False
    assert backend.sibling_anchor_count == 3
    assert backend.ancestor_path.endswith("main>ul>li>a")
    assert backend.href_shape == "acme.com|careers|2"
    assert backend.role_family is not None

    about = by_text["About Us Page"]
    assert about.in_chrome is True
    assert about.under_career_path is False

    docs = by_text["Code Governance and Compliance"]
    assert docs.same_host is False
    assert docs.in_chrome is True
    assert docs.has_job_url_hint is False


def test_job_url_hint_ignores_marketing_query_values_but_keeps_id_query_values():
    """Real regression caught live: Wiz's "Get a demo" nav link carries
    ?cta_source=careers&cta_page=/careers in its tracking params - a naive
    full-URL substring search matches "career" there and, combined with
    same_host, was enough to make this the sole "job" found on the page,
    which then blocked the Playwright fallback that would have found the
    real JS-rendered listings. A job/req id in a query VALUE (Check Point's
    ?joborderid=0936589, Wiz's own ?gh_jid=4702745006) must still count."""
    html = """
    <html><body><main>
      <a href="/demo?cta_source=careers&cta_page=/careers&cta_placement=nav">Get a demo</a>
      <a href="/careers/job/4702745006?gh_jid=4702745006">Account Executive</a>
      <a href="https://careers.checkpoint.com/index.php?a=show&joborderid=0936589">Security Engineer</a>
    </main></body></html>
    """
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL)
    by_text = {c.text: c for c in candidates}
    assert by_text["Get a demo"].has_job_url_hint is False
    assert by_text["Account Executive"].has_job_url_hint is True
    assert by_text["Security Engineer"].has_job_url_hint is True


def test_skips_fragment_javascript_and_mailto_links_and_dedupes_by_absolute_url():
    html = """
    <a href="#top">Top of page link</a>
    <a href="javascript:void(0)">Open the menu now</a>
    <a href="mailto:jobs@acme.com">Email us about jobs</a>
    <a href="/careers/one">Backend Engineer</a>
    <a href="https://acme.com/careers/one">Backend Engineer</a>
    """
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL)
    assert [c.href for c in candidates] == ["https://acme.com/careers/one"]


def test_container_selector_scopes_the_search_and_falls_back_to_the_whole_page_when_it_matches_nothing():
    html = """
    <div class="jobs"><a href="/careers/one">Backend Engineer</a></div>
    <div class="menu"><a href="/pricing">Pricing and Plans</a></div>
    """
    scoped = CandidateExtractor().extract(_page(html), CAREER_URL, container_selector="div.jobs")
    assert [c.text for c in scoped] == ["Backend Engineer"]
    fallback = CandidateExtractor().extract(_page(html), CAREER_URL, container_selector="div.nope")
    assert len(fallback) == 2


def test_prefers_a_nested_heading_over_the_whole_card_text():
    from bs4 import BeautifulSoup
    a = BeautifulSoup('<a href="/x"><h2>Senior Backend Developer</h2><span>Engineering Israel Apply Now</span></a>', "html.parser").a
    assert link_title_text(a) == "Senior Backend Developer"


def test_falls_back_to_a_sibling_heading_when_the_anchor_is_just_a_button():
    """Real DOM caught live (Appcharge): the card's title lives in a <h3>
    that is a SIBLING of the <a>, not nested inside it - a separate
    "Apply" button next to the heading, both children of one card div."""
    from bs4 import BeautifulSoup
    a = BeautifulSoup(
        '<div><div><h3>Data Analyst</h3></div><a href="/careers/data-analyst">Apply</a></div>', "html.parser"
    ).a
    assert link_title_text(a) == "Data Analyst"


def test_does_not_borrow_a_heading_from_a_sibling_card_in_a_shared_container():
    """If the anchor's parent hosts more than one anchor (a shared list
    container, not a narrow per-card wrapper), a heading found there could
    belong to a different job entirely - stay with the anchor's own text."""
    from bs4 import BeautifulSoup
    container = BeautifulSoup(
        '<ul><h3>Backend Engineer</h3><a href="/careers/one">Apply</a><a href="/careers/two">Apply</a></ul>', "html.parser"
    )
    anchors = container.find_all("a")
    assert link_title_text(anchors[1]) == "Apply"


def test_cookie_widget_links_are_dropped_and_cap_is_respected():
    html = '<div class="cookiebot"><a href="/cookies-policy">Cookie Preferences Center</a></div>' + "".join(
        f'<a href="/careers/job-{i}">Engineer number {i}</a>' for i in range(10)
    )
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL, cap=4)
    assert len(candidates) == 4
    assert all("cookies-policy" not in c.href for c in candidates)


def test_strip_non_content_leaves_the_links_the_extractor_reads():
    """Snapshots are stored stripped; the extractor must see the same page."""
    from jobfit.scrape.candidates import strip_non_content

    html = """<html><head><style>a{color:red}</style>
    <script>var jobs = [{"title": "Fake Job", "url": "/nope"}];</script></head>
    <body><svg><a href="/icon">icon</a></svg><noscript><a href="/ns">enable js</a></noscript>
    <ul><li><a href="/careers/backend-1">Senior Backend Engineer</a></li>
    <li><a href="/careers/devops-2">DevOps Engineer</a></li></ul></body></html>"""
    stripped = strip_non_content(html)
    assert "Fake Job" not in stripped and "color:red" not in stripped
    assert "/icon" not in stripped and "/ns" not in stripped

    extractor = CandidateExtractor()
    before = [(c.text, c.href) for c in extractor.extract(_page(html), CAREER_URL)]
    after = [(c.text, c.href) for c in extractor.extract(_page(stripped), CAREER_URL)]
    assert before == after
    assert [href for _, href in after] == [f"{CAREER_URL}backend-1", f"{CAREER_URL}devops-2"]
