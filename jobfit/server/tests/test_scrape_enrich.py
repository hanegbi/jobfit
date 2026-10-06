"""Detail-page enrichment: description/location/etc. from the posting's
own page (the old fetch_generic_job_details) plus the Evidence record the
health policy, the unparseable-job gate and the audit all read."""

from datetime import datetime, timezone

import pytest

from jobfit import ats_fetchers
from jobfit.scrape import enrich, errors
from jobfit.scrape.fetchers import PageFetcher, make_page
from jobfit.scrape.models import JobPosting

JOB_HTML = """
<html><head><script type="application/ld+json">{"@type": "JobPosting", "title": "Backend Engineer",
 "description": "<p>Requirements: 5+ years Python. Nice to have: Kubernetes.</p>", "datePosted": "2026-06-01",
 "employmentType": "FULL_TIME", "jobLocation": {"address": {"addressLocality": "Tel Aviv", "addressCountry": "IL"}}}</script></head>
<body><h1>Backend Engineer</h1><p>Requirements: 5+ years Python. Nice to have: Kubernetes.</p><a href="/apply/1">Apply now</a></body></html>
"""


class FakePageFetcher(PageFetcher):
    def __init__(self, pages: dict[str, tuple[int, str]], fail: set[str] = frozenset()):
        self.pages = pages
        self.fail = fail
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if url in self.fail:
            raise errors.FetchFailed(url)
        status, html = self.pages[url]
        return make_page(url, url, status, html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


def test_parse_job_details_html_extracts_jsonld_fields():
    details = ats_fetchers.parse_job_details_html(JOB_HTML)
    assert details["location"] == "Tel Aviv, IL"
    assert details["employment_type"] == "FULL_TIME"
    assert details["posted_at"] == "2026-06-01"
    assert "5+ years Python" in details["description"]


def test_extract_evidence_reads_jsonld_apply_cta_sections_role_family_and_shape():
    evidence = enrich.extract_evidence(JOB_HTML, "Backend Engineer", "https://acme.com/careers/backend-1")
    assert evidence.jsonld_jobposting is True
    assert evidence.apply_cta is True
    assert evidence.requirement_sections >= 1
    assert evidence.role_family_from_title == "backend"
    assert evidence.url_shape == "acme.com|careers|2"


def test_extract_evidence_on_a_marketing_page_is_empty():
    html = "<html><body><h1>Code Governance and Compliance</h1><p>Our platform helps teams ship safely.</p></body></html>"
    evidence = enrich.extract_evidence(html, "Code Governance and Compliance", "https://copyleaks.com/code-governance-and-compliance")
    assert evidence.jsonld_jobposting is False and evidence.apply_cta is False
    assert evidence.requirement_sections == 0 and evidence.role_family_from_title is None


def test_generic_enricher_fills_description_fields_and_evidence():
    fetcher = FakePageFetcher({"https://acme.com/careers/backend-1": (200, JOB_HTML)})
    posting = JobPosting(title="Backend Engineer", url="https://acme.com/careers/backend-1", source="html_listing")
    enriched = enrich.GenericHtmlEnricher(fetcher).enrich(posting)
    assert enriched.location == "Tel Aviv, IL" and enriched.posted_at == "2026-06-01"
    assert "Python" in enriched.description
    assert enriched.evidence.jsonld_jobposting is True
    assert posting.description == ""  # the input was not mutated


def test_generic_enricher_keeps_the_posting_on_fetch_failure_or_4xx_or_skipped_host():
    fetcher = FakePageFetcher({"https://acme.com/careers/gone": (404, "<p>gone</p>")}, fail={"https://acme.com/careers/down"})
    for url in ("https://acme.com/careers/gone", "https://acme.com/careers/down", "https://www.linkedin.com/jobs/view/1"):
        posting = JobPosting(title="Backend Engineer", url=url, source="html_listing")
        enriched = enrich.GenericHtmlEnricher(fetcher).enrich(posting)
        assert enriched.description == ""
        assert enriched.evidence.role_family_from_title == "backend"
    assert "https://www.linkedin.com/jobs/view/1" not in fetcher.calls


def test_noop_enricher_adds_minimal_evidence_only_when_missing():
    posting = JobPosting(title="Backend Engineer", url="https://boards.greenhouse.io/acme/jobs/1",
                         description="Requirements: Python", source="ats_api")
    enriched = enrich.NoopEnricher().enrich(posting)
    assert enriched.evidence.role_family_from_title == "backend"
    assert enriched.evidence.requirement_sections >= 1
    assert enriched.evidence.jsonld_jobposting is False
    again = enrich.NoopEnricher().enrich(enriched)
    assert again.evidence == enriched.evidence


CARD_TITLE = "Senior DevOps Engineer (FedRAMP) Location United States"
DETAIL_HTML = """<html><head><title>Senior DevOps Engineer (FedRAMP) | Orca</title></head>
<body><h1>Senior DevOps Engineer (FedRAMP)</h1><p>Requirements: 5+ years Linux.</p></body></html>"""


def test_enricher_takes_the_title_from_the_job_page_and_keeps_the_rest_as_location():
    """A listing card's text is title + metadata; the job's own page states
    the title alone, so the difference between them is the metadata."""
    fetcher = FakePageFetcher({"https://acme.com/jobs/1": (200, DETAIL_HTML)})
    posting = enrich.GenericHtmlEnricher(fetcher).enrich(
        JobPosting(title=CARD_TITLE, url="https://acme.com/jobs/1", source="html_listing")
    )
    assert posting.title == "Senior DevOps Engineer (FedRAMP)"
    assert posting.location == "United States"


def test_enricher_keeps_the_listing_title_when_the_page_heading_is_unrelated():
    html = "<html><body><h1>Careers at Acme</h1><p>Requirements: Python.</p></body></html>"
    fetcher = FakePageFetcher({"https://acme.com/jobs/2": (200, html)})
    posting = enrich.GenericHtmlEnricher(fetcher).enrich(
        JobPosting(title="Senior Backend Engineer", url="https://acme.com/jobs/2", source="html_listing")
    )
    assert posting.title == "Senior Backend Engineer"


def test_enricher_splits_the_card_text_when_the_job_page_cannot_be_read():
    """The card text is what the listing scrape passes in, so a failed fetch
    must still not store "Senior MLOps Engineer Full-time Senior Tel Aviv"."""
    fetcher = FakePageFetcher({}, fail={"https://acme.com/jobs/3"})
    posting = enrich.GenericHtmlEnricher(fetcher).enrich(
        JobPosting(title="Senior MLOps Engineer Full-time Senior Tel Aviv",
                   url="https://acme.com/jobs/3", source="html_listing")
    )
    assert posting.title == "Senior MLOps Engineer"
    assert posting.location == "Tel Aviv"
    assert posting.employment_type == "Full-time"


# --- a posting that is still listed but no longer live ---------------------

def test_posting_is_gone_recognises_the_four_ways_a_job_dies():
    """All four taken from one real export of 89 links: a 404, Greenhouse
    bouncing /zscaler/jobs/123 to /zscaler?error=true, Comeet bouncing a job
    back to its board, and a 200 whose body says the position is filled."""
    from jobfit.scrape.enrich import posting_is_gone

    assert posting_is_gone("https://x/job/1", "https://x/job/1", 404, "<html>x</html>") == "http 404"
    assert posting_is_gone(
        "https://job-boards.greenhouse.io/zscaler/jobs/123",
        "https://job-boards.greenhouse.io/zscaler?error=true", 200, "<html>x</html>")
    assert posting_is_gone(
        "https://www.comeet.com/jobs/rapyd/73.00E/data-analyst/44.A11",
        "https://www.comeet.com/jobs/rapyd/73.00E", 200, "<html>x</html>")
    assert posting_is_gone("https://x/j/1", "https://x/j/1", 200,
                           "<html><body>Sorry, this position is no longer available</body></html>")


def test_a_live_posting_and_a_deeper_redirect_are_not_gone():
    """The redirect rule must read direction, not movement: AppsFlyer
    rewrites /jobs/position/8732730002 to the same job with its slug
    appended, and that job is alive. Only a bounce UP to an ancestor path
    means the item itself is gone."""
    from jobfit.scrape.enrich import posting_is_gone

    assert posting_is_gone("https://x/j/1", "https://x/j/1", 200,
                           "<html><body>Senior Backend Engineer. Requirements: Python</body></html>") is None
    assert posting_is_gone(
        "https://careers.appsflyer.com/jobs/position/8732730002",
        "https://careers.appsflyer.com/jobs/position/8732730002/software-team-leader",
        200, "<html>job</html>") is None


def test_a_challenge_page_is_blocked_not_a_dead_job():
    """Cloudflare answers 200 with a page that has no job on it. Closing the
    job would be wrong - the posting may be perfectly alive behind the wall -
    so the fetch is reported as blocked and the job is left alone."""
    from jobfit.scrape.enrich import fetch_outcome, posting_is_gone

    wall = "<html><body>Attention Required! | Cloudflare</body></html>"
    assert fetch_outcome(200, wall) == "blocked"
    assert posting_is_gone("https://x/j/1", "https://x/j/1", 200, wall) is None
    assert fetch_outcome(403, "x") == "blocked"
    assert fetch_outcome(200, "   ") == "empty"
    assert fetch_outcome(200, "<html>job</html>") == "ok"


def test_work_mode_prefers_hybrid_over_the_remote_it_also_mentions():
    from jobfit.scrape.enrich import work_mode_of

    assert work_mode_of("Hybrid - 2 days remote from home") == "hybrid"
    assert work_mode_of("This is a fully remote position") == "remote"
    assert work_mode_of("On-site in Tel Aviv") == "onsite"
    assert work_mode_of("Senior Backend Engineer") is None


def test_a_whole_listing_looking_gone_is_a_site_redirect_not_a_mass_closure():
    """A site that answers its careers page for any sub-path makes every
    posting look bounced. One job bouncing is a withdrawal; all of them is
    the site's routing, and closing a company's entire roster on that is the
    one mistake here a later run cannot undo."""
    from jobfit.scrape.enrich import posting_is_gone

    # Each individually reads as gone...
    for slug in ("backend-1", "frontend-2", "devops-3"):
        assert posting_is_gone(f"https://acme.com/careers/{slug}", "https://acme.com/careers/", 200, "<html>x</html>")
    # ...and service.scrape is what refuses to act on all of them at once;
    # test_scrape_no_llm_at_runtime exercises that path end to end.
