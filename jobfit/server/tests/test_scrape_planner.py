"""ScrapePlanner.discover: probes decide most companies with no
classifier call at all; the rest are classified, induced and validated
before a plan can be marked verified. Everything here uses a fake
fetcher factory serving canned pages per (renderer, url)."""

from datetime import datetime, timedelta, timezone

import pytest

from jobfit.scrape import classifiers, errors, models
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcher, PageFetcherFactory, make_page
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.planner import PlanInducer, PlanValidator, ScrapePlanner
from jobfit.scrape.enrich import NoopEnricher

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
CAREER = "https://acme.com/careers/"
LISTING = """
<html><body>
<nav><a href="/about">About Us Page</a><a href="https://acme.com/docs/x">Read The Documentation</a></nav>
<ul>
 <li><a href="/careers/backend-engineer-1">Backend Engineer</a></li>
 <li><a href="/careers/frontend-engineer-2">Frontend Engineer</a></li>
 <li><a href="/careers/devops-engineer-3">DevOps Engineer</a></li>
</ul>
<a href="/code-governance">Code Governance and Compliance</a>
</body></html>
"""
SHELL = '<html><body><div id="root"></div><script>window.__NEXT_DATA__={}</script></body></html>'
EXTERNAL = '<html><body><p>Jobs</p><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe></body></html>'


class _Fetcher(PageFetcher):
    def __init__(self, pages, renderer):
        self.pages, self.renderer, self.calls = pages, renderer, []

    def fetch(self, url):
        self.calls.append(url)
        entry = self.pages.get((self.renderer, url))
        if entry is None:
            raise errors.FetchFailed(url)
        status, html, final = entry
        return make_page(url, final or url, status, html, self.renderer, NOW)


class _Factory(PageFetcherFactory):
    def __init__(self, pages):
        self.pages = pages
        self.http = _Fetcher(pages, "http")
        self.pw = _Fetcher(pages, "playwright")

    def build(self, renderer):
        return self.pw if renderer == "playwright" else self.http


class _Counting(classifiers.RulesPlanClassifier):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def classify(self, page, candidates, career_url):
        self.calls += 1
        return super().classify(page, candidates, career_url)


class _Failing(classifiers.PlanClassifier):
    derived_by = "llm"

    def classify(self, page, candidates, career_url):
        raise errors.ClassifierFailed("api down")


def _planner(pages, classifier=None):
    registry = default_registry(session=None)
    factory = StrategyFactory(registry=registry, fetchers=PageFetcherFactory(session=None, playwright_available=False), extractor=CandidateExtractor(),
                              enricher=NoopEnricher(), reject_patterns=[], techmap_index={}, health=HealthPolicy(), special_fetchers={}, session=None)
    classifier = classifier or _Counting()
    return ScrapePlanner(registry, _Factory(pages), CandidateExtractor(), classifier, PlanInducer(), PlanValidator(factory.chain_for),
                         special_hosts=["elbitsystemscareer.com"], now=lambda: NOW), classifier


def test_probes_decide_without_fetching_or_classifying():
    planner, classifier = _planner({})
    assert planner.discover("acme", None)[0].strategy.kind == "techmap_only"
    ats, _ = planner.discover("acme", "https://jobs.lever.co/acme")
    assert ats.strategy.kind == "ats_api" and ats.derived_by == "probe" and ats.status == "verified"
    special, _ = planner.discover("elbit", "https://elbitsystemscareer.com/")
    assert special.strategy.kind == "special_case"
    assert classifier.calls == 0


def test_http_404_and_homepage_redirect_are_verified_broken_urls_and_5xx_raises():
    planner, _ = _planner({
        ("http", "https://gone.com/careers"): (404, "<p>gone</p>", None),
        ("http", "https://home.com/careers"): (200, "<p>home</p>", "https://home.com/"),
        ("http", "https://down.com/careers"): (503, "<p>oops</p>", None),
    })
    assert planner.discover("gone", "https://gone.com/careers")[0].strategy == models.BrokenUrlStrategy(reason="http 404")
    home, _ = planner.discover("home", "https://home.com/careers")
    assert home.strategy == models.BrokenUrlStrategy(reason="redirects to homepage") and home.status == "verified"
    with pytest.raises(errors.FetchFailed):
        planner.discover("down", "https://down.com/careers")


def test_external_board_on_the_page_is_detected_before_classification():
    planner, classifier = _planner({("http", CAREER): (200, EXTERNAL, None)})
    plan, _ = planner.discover("acme", CAREER)
    assert plan.strategy == models.ExternalBoardStrategy(board_url="https://boards.greenhouse.io/embed/job_board?for=acme")
    assert plan.derived_by == "probe" and classifier.calls == 0


def test_js_shell_is_refetched_with_playwright_and_the_plan_records_the_renderer():
    planner, _ = _planner({("http", CAREER): (200, SHELL, None), ("playwright", CAREER): (200, LISTING, None)})
    plan, page = planner.discover("acme", CAREER)
    assert page.renderer == "playwright"
    assert plan.strategy.kind == "html_listing" and plan.strategy.renderer == "playwright"
    assert plan.strategy.fallbacks == ["techmap"]


def test_rules_classified_listing_is_induced_and_verified():
    planner, classifier = _planner({("http", CAREER): (200, LISTING, None)})
    plan, page = planner.discover("acme", CAREER)
    assert classifier.calls == 1
    assert plan.derived_by == "rules" and plan.status == "verified" and plan.verified_at == NOW
    s = plan.strategy
    assert s.kind == "html_listing" and s.renderer == "http"
    assert s.include_url == r"^https?://(www\.)?acme\.com/careers/[^/?#]+/?$"
    assert s.url_shape == "acme.com|careers|2"
    assert s.fallbacks == ["playwright", "techmap"]
    assert plan.labels is not None and sum(l.is_job for l in plan.labels.candidates) == 3
    assert plan.health.baseline_yield == 3
    assert plan.page_fingerprint.candidate_count == 6
    assert plan.rediscover_after == NOW + timedelta(days=7)


def test_induction_failure_falls_back_to_explicit_accept_and_unverified():
    mixed = """
    <ul><li><a href="/careers/one">Backend Engineer</a></li><li><a href="/careers/two">Frontend Engineer</a></li><li><a href="/careers/three">DevOps Engineer</a></li></ul>
    <a href="/careers/benefits">Benefits And Perks Overview</a>
    """
    # Recorded labels: the three jobs yes, "Benefits" no - but it shares the jobs' URL shape, so no pattern can separate them.
    page = make_page(CAREER, CAREER, 200, mixed, "http", NOW)
    candidates = CandidateExtractor().extract(page, CAREER)
    labels = models.Labels(page_verdict="careers_page", candidates=[
        models.CandidateLabel(index=c.index, is_job=("benefits" not in c.href), reason="fixture") for c in candidates
    ])
    planner, _ = _planner({("http", CAREER): (200, mixed, None)}, classifier=classifiers.RecordedPlanClassifier({CAREER: labels}))
    plan, _ = planner.discover("acme", CAREER)
    assert plan.status == "unverified" and plan.derived_by == "llm"
    assert plan.strategy.include_url is None
    assert sorted(plan.strategy.explicit_accept) == ["https://acme.com/careers/one", "https://acme.com/careers/three", "https://acme.com/careers/two"]


def test_not_a_careers_page_verdict_only_adds_a_note():
    labels = models.Labels(page_verdict="not_careers_page", candidates=[])
    planner, _ = _planner({("http", CAREER): (200, LISTING, None)}, classifier=classifiers.RecordedPlanClassifier({CAREER: labels}))
    plan, _ = planner.discover("acme", CAREER)
    assert plan.strategy.kind == "html_listing" and plan.status == "unverified"
    assert any("not a careers page" in n for n in plan.notes)


def test_classifier_failure_falls_back_to_rules_with_a_note():
    planner, _ = _planner({("http", CAREER): (200, LISTING, None)}, classifier=_Failing())
    plan, _ = planner.discover("acme", CAREER)
    assert plan.derived_by == "rules" and plan.status == "verified"
    assert any("classifier failed" in n for n in plan.notes)


def test_page_with_no_anchors_is_techmap_only_unverified():
    planner, classifier = _planner({("http", CAREER): (200, "<html><body><p>We are hiring soon.</p></body></html>", None)})
    plan, _ = planner.discover("acme", CAREER)
    assert plan.strategy == models.TechmapOnlyStrategy(reason="no anchors on page") and plan.status == "unverified"
    assert classifier.calls == 0


def test_inducer_handles_flat_query_schemes_and_exclude_shapes():
    html = """
    <a href="index.php?a=show&joborderid=1">Administrative Assistant</a>
    <a href="index.php?a=show&joborderid=2">Backend Developer</a>
    <a href="index.php?a=show&joborderid=3">Frontend Developer</a>
    <a href="/solutions/firewall">Next Generation Firewall</a>
    """
    url = "https://careers.checkpoint.com/index.php?q="
    page = make_page(url, url, 200, html, "http", NOW)
    candidates = CandidateExtractor().extract(page, url)
    labels = models.Labels(page_verdict="careers_page", candidates=[models.CandidateLabel(index=c.index, is_job="joborderid" in c.href, reason="f") for c in candidates])
    strategy = PlanInducer().induce(labels, candidates, page, "http")
    assert strategy.include_url == r"^https?://(www\.)?careers\.checkpoint\.com/index\.php\?.*\bjoborderid="
    assert strategy.exclude_url == [r"^https?://(www\.)?careers\.checkpoint\.com/solutions/[^/?#]+/?$"]
    assert strategy.url_shape == "careers.checkpoint.com|?a,joborderid"


def test_validator_requires_every_labelled_job_accepted_and_no_labelled_non_job_accepted():
    page = make_page(CAREER, CAREER, 200, LISTING, "http", NOW)
    candidates = CandidateExtractor().extract(page, CAREER)
    labels = classifiers.RulesPlanClassifier().classify(page, candidates, CAREER)
    factory = StrategyFactory(registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=False), extractor=CandidateExtractor(),
                              enricher=NoopEnricher(), reject_patterns=[], techmap_index={}, health=HealthPolicy(), special_fetchers={}, session=None)
    validator = PlanValidator(factory.chain_for)
    good = PlanInducer().induce(labels, candidates, page, "http")
    assert validator.validate(good, labels, candidates) is True
    too_broad = models.HtmlListingStrategy(include_url=r"^https://acme\.com/")
    assert validator.validate(too_broad, labels, candidates) is False
