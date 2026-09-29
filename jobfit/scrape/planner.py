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
from jobfit.scrape.classifiers import LISTING_LINK_PATH, LISTING_LINK_TEXT, PlanClassifier, RulesPlanClassifier
from jobfit.scrape.errors import ClassifierFailed, FetchFailed
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.filters import FilterChain
from jobfit.scrape.inline_json import find_inline_jobs
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

    def _embedded_ats(self, page: Page, company_id: str | None = None, career_url: str | None = None) -> AtsApiStrategy | None:
        """An ATS mounted in the page by script/iframe/inline config (no
        job links in the DOM) - resolved through the registry, generically."""
        from jobfit.scrape.ats.embedded import company_hint_for, find_embedded_ats_candidates

        for client, board in find_embedded_ats_candidates(page.html, self.registry, company_hint_for(company_id, career_url)):
            if getattr(client, "session", None) is not None:
                # A page can carry a stale config (Biolojic's dead Comeet token;
                # HoneyBook's old Greenhouse next to its live Ashby board). One
                # real call per candidate decides; empty or failing = next.
                try:
                    if not client.fetch_board(board):
                        continue
                except FetchFailed:
                    continue
            return AtsApiStrategy(provider=client.provider, board=board, board_url=client.board_url(board))
        return None

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
        embedded = self._embedded_ats(page, company_id, career_url)
        if embedded:
            return self._probe_plan(company_id, career_url, embedded), page

        notes: list[str] = []
        candidates = self.extractor.extract(page, career_url, cap=200)
        if not candidates:
            inline_plan = self._inline_json_plan(company_id, career_url, page, renderer, notes, 0)
            if inline_plan is not None:
                return inline_plan, page
            return self._probe_plan(company_id, career_url, TechmapOnlyStrategy(reason="no anchors on page"), status="unverified"), page

        plan, page, candidates = self._html_plan(company_id, career_url, page, renderer, candidates, notes)
        landing_yield = plan.health.baseline_yield or 0
        if plan.strategy.kind != "html_listing" or landing_yield > 2:
            return plan, page

        inline_plan = self._inline_json_plan(company_id, career_url, page, renderer, notes, landing_yield)
        if inline_plan is not None:
            return inline_plan, page

        # (Almost) nothing on the registered page - is it a landing page with
        # the real listing one hop away ("See open roles" -> /careers/jobs)?
        # A stray accepted link or two is typical of a landing page (a benefits
        # page, a "join our talent network" link); the hop must beat it.
        hop_url = self._listing_hop(candidates, career_url)
        if hop_url:
            hop_page = None
            try:
                hop_page = self.fetchers.build("http").fetch(hop_url)
            except FetchFailed as error:
                notes.append(f"listing hop {hop_url} failed: {error}")
            if hop_page is not None and hop_page.status < 400:
                embedded = self._embedded_ats(hop_page, company_id, career_url)
                if embedded:
                    notes.append(f"listing hop {hop_url} embeds an ATS")
                    return self._probe_plan(company_id, career_url, embedded), hop_page
                hop_renderer: Renderer = "http"
                if hop_page.is_js_shell:
                    hop_page = self.fetchers.build("playwright").fetch(hop_url)
                    hop_renderer = "playwright"
                hop_candidates = self.extractor.extract(hop_page, hop_url, cap=200)
                if hop_candidates:
                    hop_plan, hop_page, _ = self._html_plan(company_id, career_url, hop_page, hop_renderer, hop_candidates, notes, listing_url=hop_url)
                    if hop_plan.strategy.kind != "html_listing" or (hop_plan.health.baseline_yield or 0) > landing_yield:
                        notes.append(f"listing found one hop away at {hop_url}")
                        return hop_plan.model_copy(update={"notes": list(notes)}), hop_page

        # Still nothing and we only looked at the plain-HTTP page: render it.
        # Tufin's Comeet widget, Apono's ?job_uid links and Nuvei's 58 Workable
        # links only exist in the rendered DOM; the shell heuristic misses
        # pages whose static text is long enough to look complete.
        if renderer == "http":
            rendered = None
            try:
                rendered = self.fetchers.build("playwright").fetch(career_url)
            except FetchFailed as error:
                notes.append(f"render failed: {error}")
            if rendered is not None and rendered.status < 400 and rendered.html != page.html:
                embedded = self._embedded_ats(rendered, company_id, career_url)
                if embedded:
                    notes.append("ATS found only in the rendered page")
                    return self._probe_plan(company_id, career_url, embedded), rendered
                r_candidates = self.extractor.extract(rendered, career_url, cap=200)
                if r_candidates:
                    r_plan, rendered, _ = self._html_plan(company_id, career_url, rendered, "playwright", r_candidates, notes)
                    if r_plan.strategy.kind != "html_listing" or (r_plan.health.baseline_yield or 0) > landing_yield:
                        notes.append("listing found only in the rendered page")
                        return r_plan.model_copy(update={"notes": list(notes)}), rendered
                inline_plan = self._inline_json_plan(company_id, career_url, rendered, "playwright", notes, landing_yield)
                if inline_plan is not None:
                    return inline_plan, rendered
        return plan, page

    def _inline_json_plan(self, company_id: str, career_url: str, page: Page, renderer: Renderer, notes: list[str],
                          beat: int) -> ScrapePlan | None:
        """Jobs inlined as page JSON (Next.js/Nuxt SPAs render them as rows
        with no links): the runtime's InlineJsonScrape fallback reads them;
        the plan just records that this page yields that way. None unless
        the inlined list beats `beat` (what link scraping found)."""
        inline = find_inline_jobs(page.html, career_url)
        if len(inline) <= beat:
            return None
        now = self.now()
        plan = ScrapePlan(company_id=company_id, career_url=career_url, derived_by="probe", derived_at=now, verified_at=now, status="verified",
                          strategy=HtmlListingStrategy(renderer=renderer, fallbacks=["techmap"]),
                          notes=notes + [f"{len(inline)} jobs inlined as page JSON (read by the inline-json fallback)"])
        plan.health.baseline_yield = len(inline)
        return plan

    def _listing_hop(self, candidates: list[Candidate], career_url: str) -> str | None:
        """The one same-site link most likely to be the actual job listing."""
        base_host = _host(career_url)
        base_norm = career_url.split("#", 1)[0].rstrip("/").lower()
        scored: list[tuple[int, int, str]] = []
        for c in candidates:
            href = c.href.split("#", 1)[0]
            if not href or href.rstrip("/").lower() == base_norm:
                continue
            host = _host(href)
            if not (host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)):
                continue
            path = urlsplit(href).path
            score = 0
            if LISTING_LINK_PATH.search(path):
                score += 2
            if LISTING_LINK_TEXT.search((c.text or "").strip()):
                score += 1
            if score:
                scored.append((score, -c.index, href))
        if not scored:
            return None
        scored.sort(reverse=True)
        return scored[0][2]

    def _html_plan(self, company_id: str, career_url: str, page: Page, renderer: Renderer, candidates: list[Candidate],
                   notes: list[str], listing_url: str | None = None) -> tuple[ScrapePlan, Page, list[Candidate]]:
        base_url = listing_url or career_url
        labels, derived_by = self._classify(page, candidates, base_url, notes)
        if labels.page_verdict == "js_shell" and renderer == "http":
            page = self.fetchers.build("playwright").fetch(base_url)
            renderer = "playwright"
            candidates = self.extractor.extract(page, base_url, cap=200)
            labels, derived_by = self._classify(page, candidates, base_url, notes)
        if labels.page_verdict == "external_board" and labels.external_board_url and self.registry.resolve(labels.external_board_url):
            plan = self._probe_plan(company_id, career_url, ExternalBoardStrategy(board_url=labels.external_board_url))
            return plan.model_copy(update={"derived_by": derived_by, "labels": labels}), page, candidates
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
        if listing_url:
            strategy = strategy.model_copy(update={"listing_url": listing_url})

        plan = ScrapePlan(
            company_id=company_id, career_url=career_url, derived_by=derived_by, model=getattr(self.classifier, "model", None),
            derived_at=now, verified_at=verified_at, status=status, strategy=strategy, labels=labels,
            page_fingerprint=page_fingerprint(candidates), rediscover_after=now + self.cooldown, notes=notes,
        )
        plan.health.baseline_yield = len(accepted_hrefs)
        return plan, page, candidates
