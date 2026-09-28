"""The discovery batch: which companies get (re)discovered, in what
order, under what cap; plans and snapshots written; failures recorded."""

from datetime import datetime, timedelta, timezone

import pytest

from jobfit import config
from jobfit.scrape import errors, models
from jobfit.scrape.fetchers import make_page
from jobfit.scrape.plan_store import MemoryPlanStore
from jobfit.scripts import update_jobs

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _plan(company_id, status="verified", derived_by="llm", rediscover_after=None):
    return models.ScrapePlan(company_id=company_id, career_url=f"https://{company_id}.com/careers", derived_by=derived_by, derived_at=NOW,
                             status=status, strategy=models.HtmlListingStrategy(), rediscover_after=rediscover_after)


def test_selection_order_missing_then_stale_then_rules_unverified_with_cooldown_and_cap():
    store = MemoryPlanStore()
    store.put(_plan("verified_co"))
    store.put(_plan("stale_co", status="stale_suspect", rediscover_after=NOW - timedelta(days=1)))
    store.put(_plan("cooling_co", status="stale_suspect", rediscover_after=NOW + timedelta(days=3)))
    store.put(_plan("rules_co", status="unverified", derived_by="rules"))
    store.put(_plan("schema_co", status="stale"))
    companies = {"Missing Co": "https://missing.com/careers", "Verified Co": "https://verified_co.com/careers", "Stale Co": "https://stale_co.com/careers",
                 "Cooling Co": "https://cooling_co.com/careers", "Rules Co": "https://rules_co.com/careers", "Schema Co": "https://schema_co.com/careers"}
    selected = update_jobs.select_for_discovery(companies, store, NOW, max_per_run=0)
    assert [name for name, _ in selected] == ["Missing Co", "Stale Co", "Schema Co", "Rules Co"]
    assert [name for name, _ in update_jobs.select_for_discovery(companies, store, NOW, max_per_run=2)] == ["Missing Co", "Stale Co"]
    assert update_jobs.select_for_discovery(companies, store, NOW, max_per_run=0, force_company="Cooling Co") == [("Cooling Co", "https://cooling_co.com/careers")]


class _Planner:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def discover(self, company_id, career_url):
        if company_id in self.fail:
            raise errors.FetchFailed(company_id)
        status = "verified" if career_url else "unverified"
        plan = models.ScrapePlan(company_id=company_id, career_url=career_url, derived_by="rules", derived_at=NOW, status=status,
                                 strategy=models.HtmlListingStrategy() if career_url else models.TechmapOnlyStrategy(reason="x"))
        page = make_page(career_url, career_url, 200, "<p>snap</p>", "http", NOW) if career_url else None
        return plan, page


def test_discover_plans_writes_plans_and_snapshots_and_records_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".lock")
    store = MemoryPlanStore()
    companies = {"Acme": "https://acme.com/careers", "Beta": None, "Down": "https://down.com/careers"}
    stats = update_jobs.discover_plans(companies, _Planner(fail={"down"}), store, tmp_path / "snaps", max_per_run=0, concurrency=2, now=lambda: NOW)
    assert stats.selected == 3 and stats.discovered == 2 and stats.failed == ["Down"]
    assert stats.verified == 1 and stats.unverified == 1
    assert store.get("acme").status == "verified" and store.get("beta").status == "unverified"
    assert (tmp_path / "snaps" / "acme.html").read_text(encoding="utf-8") == "<p>snap</p>"
    assert not (tmp_path / "snaps" / "beta.html").exists()


def test_cli_rediscover_requires_company(monkeypatch):
    monkeypatch.setattr("sys.argv", ["update_jobs", "--rediscover"])
    with pytest.raises(SystemExit):
        update_jobs.main()
