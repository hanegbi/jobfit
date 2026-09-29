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
