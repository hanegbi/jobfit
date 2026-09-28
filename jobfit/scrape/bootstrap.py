"""Composition root. build_scrape_service() is the production runtime
graph and contains no classifier of any kind. build_discovery_planner()
(Task 14/16) is the only function that ever constructs a model client,
and it imports jobfit.scrape.llm_client lazily inside its body."""

from __future__ import annotations

import json
from pathlib import Path

from jobfit import ats_fetchers, config
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import GenericHtmlEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import CachedPageFetcher, HttpPageFetcher, PageFetcherFactory
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.plan_store import FilePlanStore
from jobfit.scrape.service import CompanyScrapeService


def load_reject_patterns(path: Path | None = None) -> list[str]:
    path = path or config.LINK_REJECTS_PATH
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("patterns", []))
    except (OSError, ValueError):
        return []


def build_scrape_service(session, techmap_index: dict[str, list[dict]], plans_dir: Path | None = None,
                         playwright_available: bool = True) -> CompanyScrapeService:
    registry = default_registry(session)
    fetchers = PageFetcherFactory(session, playwright_available)
    enricher = GenericHtmlEnricher(CachedPageFetcher(HttpPageFetcher(session), config.PAGE_CACHE_DIR, config.PAGE_CACHE_TTL_HOURS))
    health = HealthPolicy()
    factory = StrategyFactory(
        registry=registry, fetchers=fetchers, extractor=CandidateExtractor(), enricher=enricher,
        reject_patterns=load_reject_patterns(), techmap_index=techmap_index, health=health,
        special_fetchers=ats_fetchers.SPECIAL_CASE_FETCHERS, session=session,
    )
    store = FilePlanStore(plans_dir or config.SCRAPE_PLANS_DIR)
    return CompanyScrapeService(store, factory, health, registry, special_hosts=list(ats_fetchers.SPECIAL_CASE_FETCHERS))


def build_discovery_planner(session, classifier=None, playwright_available: bool = True, use_llm: bool = False, model: str | None = None):
    """The discovery graph. classifier precedence: an explicit `classifier`;
    else, with use_llm, an LLMPlanClassifier over AnthropicLLMClient
    (imported lazily HERE and nowhere else); else RulesPlanClassifier. If
    the model client cannot be constructed (no credentials), log a warning
    and fall back to rules so discovery still produces plans."""
    import logging
    from jobfit.scrape.classifiers import RulesPlanClassifier
    from jobfit.scrape.planner import PlanInducer, PlanValidator, ScrapePlanner

    if classifier is None and use_llm:
        try:
            from jobfit.scrape.classifiers import LLMPlanClassifier
            from jobfit.scrape.llm_client import AnthropicLLMClient
            model = model or config.SCRAPE_PLAN_LLM_MODEL
            classifier = LLMPlanClassifier(AnthropicLLMClient(model), model)
        except Exception as error:  # noqa: BLE001 - missing SDK or credentials: degrade to rules, loudly
            logging.getLogger("jobfit.scrape.discovery").warning("LLM classifier unavailable (%s); using rules", error)
            classifier = None

    registry = default_registry(session)
    fetchers = PageFetcherFactory(session, playwright_available)
    factory = StrategyFactory(
        registry=registry, fetchers=fetchers, extractor=CandidateExtractor(), enricher=GenericHtmlEnricher(HttpPageFetcher(session)),
        reject_patterns=load_reject_patterns(), techmap_index={}, health=HealthPolicy(),
        special_fetchers=ats_fetchers.SPECIAL_CASE_FETCHERS, session=session,
    )
    return ScrapePlanner(
        registry, fetchers, CandidateExtractor(), classifier or RulesPlanClassifier(), PlanInducer(), PlanValidator(factory.chain_for),
        special_hosts=list(ats_fetchers.SPECIAL_CASE_FETCHERS), cooldown_days=config.DISCOVERY_COOLDOWN_DAYS,
    )
