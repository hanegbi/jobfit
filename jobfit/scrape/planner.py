"""Discovery: (company, career url) -> ScrapePlan. Probes first (no
classifier involved), then classification, induction and validation. The
classifier is injected - RulesPlanClassifier here, LLMPlanClassifier from
the discovery command - and the planner never knows which it got beyond
its `derived_by` label."""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.scrape.ats import AtsRegistry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.classifiers import PlanClassifier, RulesPlanClassifier
from jobfit.scrape.errors import ClassifierFailed, FetchFailed
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.filters import FilterChain
from jobfit.scrape.models import (
    AtsApiStrategy, BrokenUrlStrategy, Candidate, ExternalBoardStrategy, HtmlListingStrategy, Labels, Page,
    Renderer, ScrapePlan, SpecialCaseStrategy, Strategy, TechmapOnlyStrategy,
)
from jobfit.scrape.strategies import page_fingerprint

logger = logging.getLogger("jobfit.scrape.discovery")
MAX_EXCLUDES = 10


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


class PlanInducer:
    """Labels + candidates -> HtmlListingStrategy, deterministically."""

    def induce(self, labels: Labels, candidates: list[Candidate], page: Page, renderer: Renderer) -> HtmlListingStrategy:
        by_index = {c.index: c for c in candidates}
        accepted = [by_index[l.index] for l in labels.candidates if l.is_job and l.index in by_index]
        rejected = [by_index[l.index] for l in labels.candidates if not l.is_job and l.index in by_index]
        fallbacks = ["playwright", "techmap"] if renderer == "http" else ["techmap"]
        if not accepted:
            return HtmlListingStrategy(renderer=renderer, fallbacks=fallbacks)
        url_shape = Counter(c.href_shape for c in accepted).most_common(1)[0][0]
        return HtmlListingStrategy(
            renderer=renderer,
            container_selector=labels.container_selector if self._selector_ok(labels.container_selector, page, accepted) else None,
            include_url=self._include_pattern(accepted),
            exclude_url=self._exclude_patterns(accepted, rejected),
            url_shape=url_shape,
            fallbacks=fallbacks,
        )

    @staticmethod
    def _include_pattern(accepted: list[Candidate]) -> str | None:
        hosts = {_host(c.href) for c in accepted}
        if len(hosts) != 1:
            return None
        host = re.escape(hosts.pop())
        parsed = [urlsplit(c.href) for c in accepted]
        if all(p.query and len(_segments(c.href)) <= 1 for p, c in zip(parsed, accepted)):
            paths = {p.path for p in parsed}
            if len(paths) != 1:
                return None
            key_sets = [set(parse_qs(p.query, keep_blank_values=True)) for p in parsed]
            common = set.intersection(*key_sets)
            if not common:
                return None
            # the key whose values vary most is the job id
            key = max(sorted(common), key=lambda k: len({parse_qs(p.query).get(k, [""])[0] for p in parsed}))
            return rf"^https?://(www\.)?{host}{re.escape(paths.pop())}\?.*\b{re.escape(key)}="
        parents = [_segments(c.href)[:-1] for c in accepted]
        prefix: list[str] = []
        for parts in zip(*parents):
            if len(set(parts)) == 1:
                prefix.append(parts[0])
            else:
                break
        if not prefix:
            return None
        depths = {len(_segments(c.href)) for c in accepted}
        tail = r"[^/?#]+/?$" if depths == {len(prefix) + 1} else r"[^?#]+$"
        return rf"^https?://(www\.)?{host}/{'/'.join(re.escape(s) for s in prefix)}/{tail}"

    @staticmethod
    def _exclude_patterns(accepted: list[Candidate], rejected: list[Candidate]) -> list[str]:
        accepted_shapes = {c.href_shape for c in accepted}
        patterns: list[str] = []
        seen: set[str] = set()
        for c in rejected:
            shape = c.href_shape
            if shape in accepted_shapes or shape in seen or "|?" in shape:
                continue
            seen.add(shape)
            host, parent, _depth = shape.split("|")
            body = "/".join(re.escape(s) for s in parent.split("/") if s)
            patterns.append(rf"^https?://(www\.)?{re.escape(host)}/{body + '/' if body else ''}[^/?#]+/?$")
            if len(patterns) >= MAX_EXCLUDES:
                break
        return patterns

    @staticmethod
    def _selector_ok(selector: str | None, page: Page, accepted: list[Candidate]) -> bool:
        if not selector:
            return False
        try:
            elements = BeautifulSoup(page.html, "html.parser").select(selector)
        except Exception:  # noqa: BLE001 - an invalid selector is simply not used
            return False
        wanted = {c.href for c in accepted}
        for el in elements:
            for a in el.find_all("a", href=True):
                if urljoin(page.url, a["href"]) in wanted:
                    return True
        return False


class PlanValidator:
    def __init__(self, chain_builder: Callable[[HtmlListingStrategy], FilterChain]):
        self.chain_builder = chain_builder

    def validate(self, strategy: HtmlListingStrategy, labels: Labels, candidates: list[Candidate]) -> bool:
        accepted, _ = self.chain_builder(strategy).run(candidates)
        got = {c.index for c in accepted}
        want = {l.index for l in labels.candidates if l.is_job}
        forbid = {l.index for l in labels.candidates if not l.is_job}
        return want <= got and not (forbid & got)


class ScrapePlanner:
    def __init__(self, registry: AtsRegistry, fetchers: PageFetcherFactory, extractor: CandidateExtractor,
                 classifier: PlanClassifier, inducer: PlanInducer, validator: PlanValidator, special_hosts: list[str],
                 now: Callable[[], datetime] | None = None, cooldown_days: int = 7):
        self.registry, self.fetchers, self.extractor = registry, fetchers, extractor
        self.classifier, self.inducer, self.validator = classifier, inducer, validator
        self.special_hosts = special_hosts
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.cooldown = timedelta(days=cooldown_days)
        self._rules = RulesPlanClassifier()

    def _probe_plan(self, company_id: str, career_url: str | None, strategy: Strategy, status: str = "verified") -> ScrapePlan:
        now = self.now()
        return ScrapePlan(company_id=company_id, career_url=career_url, derived_by="probe", derived_at=now,
                          verified_at=now if status == "verified" else None, status=status, strategy=strategy)

    def _external_board(self, page: Page) -> str | None:
        soup = BeautifulSoup(page.html, "html.parser")
        counts: Counter[str] = Counter()
        for tag in soup.find_all(["a", "iframe"]):
            target = tag.get("href") or tag.get("src")
            if not target:
                continue
            absolute = urljoin(page.url, target)
            if self.registry.resolve(absolute) is not None:
                counts[absolute] += 1
        if not counts:
            return None
        return counts.most_common(1)[0][0]

    def _jsonld_urls(self, page: Page) -> set[str]:
        soup = BeautifulSoup(page.html, "html.parser")
        return {urljoin(page.url, p["url"]) for p in ats_fetchers._jsonld_job_postings(soup) if isinstance(p.get("url"), str)}

    def _classify(self, page: Page, candidates: list[Candidate], career_url: str, notes: list[str]) -> tuple[Labels, str]:
        try:
            return self.classifier.classify(page, candidates, career_url), self.classifier.derived_by
        except ClassifierFailed as error:
            notes.append(f"classifier failed ({error}); fell back to rules")
            return self._rules.classify(page, candidates, career_url), "rules"

    def discover(self, company_id: str, career_url: str | None) -> tuple[ScrapePlan, Page | None]:
        if not career_url:
            return self._probe_plan(company_id, None, TechmapOnlyStrategy(reason="no career url")), None
        resolved = self.registry.resolve(career_url)
        if resolved is not None:
            client, board = resolved
            return self._probe_plan(company_id, career_url, AtsApiStrategy(provider=client.provider, board=board, board_url=client.board_url(board))), None
        for host in self.special_hosts:
            if host in career_url.lower():
                return self._probe_plan(company_id, career_url, SpecialCaseStrategy(host_fragment=host)), None

        page = self.fetchers.build("http").fetch(career_url)
        if page.status in (404, 410):
            return self._probe_plan(company_id, career_url, BrokenUrlStrategy(reason=f"http {page.status}")), page
        if page.status >= 400:
            raise FetchFailed(f"{career_url}: http {page.status}")
        if urlsplit(page.url).path in ("", "/") and urlsplit(career_url).path not in ("", "/"):
            return self._probe_plan(company_id, career_url, BrokenUrlStrategy(reason="redirects to homepage")), page
        renderer: Renderer = "http"
        if page.is_js_shell:
            page = self.fetchers.build("playwright").fetch(career_url)
            renderer = "playwright"

        board_url = self._external_board(page)
        if board_url:
            return self._probe_plan(company_id, career_url, ExternalBoardStrategy(board_url=board_url)), page

        notes: list[str] = []
        candidates = self.extractor.extract(page, career_url, cap=200)
        if not candidates:
            return self._probe_plan(company_id, career_url, TechmapOnlyStrategy(reason="no anchors on page"), status="unverified"), page

        labels, derived_by = self._classify(page, candidates, career_url, notes)
        if labels.page_verdict == "js_shell" and renderer == "http":
            page = self.fetchers.build("playwright").fetch(career_url)
            renderer = "playwright"
            candidates = self.extractor.extract(page, career_url, cap=200)
            labels, derived_by = self._classify(page, candidates, career_url, notes)
        if labels.page_verdict == "external_board" and labels.external_board_url and self.registry.resolve(labels.external_board_url):
            plan = self._probe_plan(company_id, career_url, ExternalBoardStrategy(board_url=labels.external_board_url))
            return plan.model_copy(update={"derived_by": derived_by, "labels": labels}), page
        if labels.page_verdict == "not_careers_page":
            notes.append("classifier: not a careers page - confirm in the audit and set broken_url by hand if so")

        seeds = self._jsonld_urls(page)
        if seeds:
            by_index = {c.index: c for c in candidates}
            labels = labels.model_copy(update={"candidates": [
                l.model_copy(update={"is_job": True, "reason": "json-ld JobPosting"}) if l.index in by_index and by_index[l.index].href in seeds else l
                for l in labels.candidates
            ]})

        strategy = self.inducer.induce(labels, candidates, page, renderer)
        now = self.now()
        accepted_hrefs = [c.href for c in candidates if any(l.index == c.index and l.is_job for l in labels.candidates)]
        if accepted_hrefs and self.validator.validate(strategy, labels, candidates):
            status, verified_at = "verified", now
        else:
            strategy = strategy.model_copy(update={"include_url": None, "explicit_accept": accepted_hrefs})
            status, verified_at = "unverified", None

        plan = ScrapePlan(
            company_id=company_id, career_url=career_url, derived_by=derived_by, model=getattr(self.classifier, "model", None),
            derived_at=now, verified_at=verified_at, status=status, strategy=strategy, labels=labels,
            page_fingerprint=page_fingerprint(candidates), rediscover_after=now + self.cooldown, notes=notes,
        )
        plan.health.baseline_yield = len(accepted_hrefs)
        return plan, page
