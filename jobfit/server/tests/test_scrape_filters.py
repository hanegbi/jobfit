"""Each LinkFilter in isolation, then the chain. The cases here are the
ones test_listing_heuristics.py used to pin (that file is deleted in
Task 5) plus the new evidence/shape filters."""

from jobfit.scrape import filters
from jobfit.scrape.models import Candidate


def _cand(text="Senior Backend Engineer", href="https://acme.com/careers/backend-1", index=0, **overrides):
    base = dict(
        index=index, text=text, href=href, ancestor_path="body>main>ul>li>a", sibling_anchor_count=3,
        same_host=True, under_career_path=True, has_job_url_hint=True, role_family="backend",
        in_chrome=False, href_shape="acme.com|careers|2",
    )
    base.update(overrides)
    return Candidate(**base)


# --- DenylistFilter (the old looks_like_job_title) -------------------------

def test_text_ok_accepts_a_real_title_and_rejects_nav_phrases_lengths_emails_urls():
    ok = filters.DenylistFilter.text_ok
    assert ok("Senior Backend Engineer") is True
    assert ok("Learn More") is False
    assert ok("View All") is False
    assert ok("White Papers") is False
    assert ok("Case Studies") is False
    assert ok("QA") is False
    assert ok("x" * 130) is False
    assert ok("jobs@acme.com") is False
    assert ok("12345678") is False
    assert ok("https://www.simplex-mapping.com/") is False
    assert ok("www.example.com/careers") is False


def test_text_ok_accepts_a_hebrew_title():
    assert filters.DenylistFilter.text_ok("מהנדס תוכנה בכיר") is True


def test_denylist_filter_rejects_bad_text_and_has_no_opinion_on_good_text():
    f = filters.DenylistFilter()
    assert f.accept(_cand(text="Learn More"), []).accept is False
    assert f.accept(_cand(), []) is None


# --- HrefMarkerFilter ------------------------------------------------------

def test_href_marker_filter_rejects_maps_docs_blog_resources():
    f = filters.HrefMarkerFilter()
    for href in (
        "https://www.google.com/maps/place/HaMasger+St+35", "https://maps.google.com/?q=Tel+Aviv", "https://goo.gl/maps/abc123",
        "https://coralogix.com/docs/opentelemetry/getting-started/", "https://acme.com/blog/how-we-scaled", "https://acme.com/resources/whitepaper",
    ):
        assert f.accept(_cand(href=href), []).accept is False, href
    assert f.accept(_cand(href="https://acme.com/careers/backend-engineer"), []) is None


# --- RejectListFilter ------------------------------------------------------

def test_reject_list_filter_uses_regexes():
    f = filters.RejectListFilter([r"^https://copyleaks\.com/[a-z0-9-]+$"])
    assert f.accept(_cand(href="https://copyleaks.com/code-governance-and-compliance"), []).accept is False
    assert f.accept(_cand(href="https://copyleaks.com/careers/backend-1"), []) is None


# --- CategoryPrefixFilter (the old drop_category_prefix_links) --------------

def test_category_prefix_filter_drops_an_overview_that_is_a_prefix_of_a_sibling_posting():
    overview = _cand(text="Engineering Jobs", href="https://acme.com/careers/engineering/all", index=0)
    posting = _cand(href="https://acme.com/careers/engineering/123/backend-engineer/all", index=1)
    f = filters.CategoryPrefixFilter()
    assert f.accept(overview, [overview, posting]).accept is False
    assert f.accept(posting, [overview, posting]) is None


def test_category_prefix_filter_keeps_flat_query_string_links_and_unrelated_siblings():
    a = _cand(href="https://careers.checkpoint.com/index.php?a=show&joborderid=1", index=0)
    b = _cand(href="https://careers.checkpoint.com/index.php?a=show&joborderid=2", index=1)
    f = filters.CategoryPrefixFilter()
    assert f.accept(a, [a, b]) is None and f.accept(b, [a, b]) is None
    c = _cand(href="https://acme.com/careers/eng/1/backend-engineer", index=0)
    d = _cand(href="https://acme.com/careers/eng/2/frontend-engineer", index=1)
    assert f.accept(c, [c, d]) is None and f.accept(d, [c, d]) is None


# --- PlanPatternFilter -----------------------------------------------------

def test_plan_pattern_filter_excludes_then_includes_then_explicit_then_no_opinion():
    f = filters.PlanPatternFilter(
        include_url=r"^https://acme\.com/careers/[a-z0-9-]+$", exclude_url=[r"^https://acme\.com/[a-z0-9-]+$"],
        explicit_accept=["https://acme.com/jobs/special"],
    )
    assert f.accept(_cand(href="https://acme.com/code-governance"), []).accept is False
    assert f.accept(_cand(href="https://acme.com/careers/backend-1"), []).accept is True
    assert f.accept(_cand(href="https://acme.com/jobs/special"), []).accept is True
    assert f.accept(_cand(href="https://acme.com/team/people/dan"), []) is None


# --- UrlShapeClusterFilter -------------------------------------------------

def test_url_shape_filter_with_an_expected_shape_accepts_matches_only():
    f = filters.UrlShapeClusterFilter("acme.com|careers|2")
    assert f.accept(_cand(), []).accept is True
    assert f.accept(_cand(href_shape="acme.com||1"), []) is None


def test_url_shape_filter_in_batch_mode_rejects_a_singleton_shape_without_a_job_hint():
    jobs = [_cand(index=i, href=f"https://acme.com/careers/job-{i}") for i in range(3)]
    marketing = _cand(index=3, text="Code Governance and Compliance", href="https://acme.com/code-governance", href_shape="acme.com||1", has_job_url_hint=False, role_family=None, under_career_path=False)
    f = filters.UrlShapeClusterFilter(None)
    batch = jobs + [marketing]
    assert f.accept(marketing, batch).accept is False
    assert f.accept(jobs[0], batch) is None


def test_url_shape_filter_in_batch_mode_keeps_a_singleton_that_has_a_job_hint():
    jobs = [_cand(index=i, href=f"https://acme.com/careers/job-{i}") for i in range(3)]
    odd = _cand(index=3, href="https://acme.com/jobs/12345", href_shape="acme.com|jobs|2")
    assert filters.UrlShapeClusterFilter(None).accept(odd, jobs + [odd]) is None


# --- EvidenceThresholdFilter -----------------------------------------------

def test_evidence_threshold_accepts_with_two_signals_and_rejects_chrome():
    f = filters.EvidenceThresholdFilter(min_signals=2, reject_chrome=True)
    assert f.accept(_cand(), []).accept is True
    weak = _cand(same_host=False, under_career_path=False, has_job_url_hint=False, role_family=None, sibling_anchor_count=1)
    assert f.accept(weak, []) is None
    assert f.accept(_cand(in_chrome=True), []).accept is False


def test_evidence_threshold_at_zero_signals_with_chrome_allowed_accepts_everything():
    f = filters.EvidenceThresholdFilter(min_signals=0, reject_chrome=False)
    assert f.accept(_cand(in_chrome=True, same_host=False, role_family=None), []).accept is True


# --- FilterChain -----------------------------------------------------------

def test_chain_stops_at_the_first_verdict_and_denies_by_default():
    chain = filters.FilterChain([filters.DenylistFilter(), filters.HrefMarkerFilter()])
    good, bad_text, docs = _cand(index=0), _cand(index=1, text="Learn More"), _cand(index=2, href="https://acme.com/docs/x")
    accepted, rejected = chain.run([good, bad_text, docs])
    assert accepted == []  # nothing accepted it, so deny by default
    reasons = {c.index: v.filter_name for c, v in rejected}
    assert reasons == {0: "chain", 1: "denylist", 2: "href_marker"}


def test_chain_accepts_when_a_filter_accepts_and_preserves_order():
    chain = filters.FilterChain([filters.DenylistFilter(), filters.EvidenceThresholdFilter(min_signals=0, reject_chrome=False)])
    a, b = _cand(index=0), _cand(index=1, href="https://acme.com/careers/x-2")
    accepted, rejected = chain.run([b, a])
    assert [c.index for c in accepted] == [1, 0]
    assert rejected == []
