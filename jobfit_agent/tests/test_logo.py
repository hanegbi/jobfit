"""A company mark must survive a missing domain, an ATS url and an offline reader."""

from jobfit_agent.agent.report import build, render
from jobfit_agent.tests.sample import sample_report


def _brief(career_url):
    return {"job": {"id": "x", "company_id": "c", "company": "C", "career_url": career_url},
            "scores": {}, "referrals": {}, "costs": []}


def test_domain_comes_from_the_careers_page_without_www():
    assert build.company_domain(_brief("https://www.conifers.ai/careers/")) == "conifers.ai"
    assert build.company_domain(_brief("https://jobs.lever.co/acme")) == "jobs.lever.co"


def test_a_company_with_no_careers_url_has_no_domain():
    assert build.company_domain(_brief(None)) is None
    assert build.company_domain(_brief("")) is None


def test_the_report_carries_one_domain_per_company():
    briefs = [_brief(None), _brief("https://www.acme.com/jobs")]
    report = build.build_report(briefs=briefs, research={}, costs=[], profile="p", now="n")
    assert report["companies"][0]["domain"] == "acme.com"     # the one job that knows wins


def test_the_page_renders_a_monogram_and_a_lazy_remote_logo():
    html = render.render_html(sample_report())
    assert '.mark' in html and 'class: "mark"' in html and "favicons" in html
    assert 'loading: "lazy"' in html and 'referrerpolicy: "no-referrer"' in html
