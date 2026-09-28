"""The audit turns "eyeball the UI for fake jobs" into a ranked queue,
and a decision into a persistent reject pattern the scraper enforces."""

import json
from datetime import datetime, timezone

from jobfit.scrape import models
from jobfit.scrape.plan_store import MemoryPlanStore
from jobfit.scripts import audit_scrape

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
CHROME = "Skip to main content AI platform Solutions Resources Enterprise Pricing Log in Contact sales " * 20


def _plan(url_shape="acme.com|careers|2"):
    return models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="llm", derived_at=NOW, status="verified",
                             strategy=models.HtmlListingStrategy(url_shape=url_shape))


def test_reasons_flag_missing_evidence_unclassifiable_title_off_shape_url_and_site_chrome():
    job = {"title": "Contact sales", "url": "https://acme.com/sales/contact-us", "description": CHROME,
           "job_evidence": {"jsonld_jobposting": False, "apply_cta": False, "requirement_sections": 0, "role_family_from_title": None, "url_shape": "acme.com|sales|2"}}
    reasons = audit_scrape.suspicion_reasons(job, [CHROME + " extra"], _plan())
    assert set(reasons) == {"no evidence", "title is not a role", "url off plan shape", "description duplicates sibling (site chrome)"}


def test_a_real_job_has_no_reasons():
    job = {"title": "Backend Engineer", "url": "https://acme.com/careers/backend-1", "description": "Requirements: 5+ years Python",
           "job_evidence": {"jsonld_jobposting": True, "apply_cta": True, "requirement_sections": 1, "role_family_from_title": "backend", "url_shape": "acme.com|careers|2"}}
    assert audit_scrape.suspicion_reasons(job, ["Requirements: 3+ years Go"], _plan()) == []


def test_audit_ranks_companies_jobs_by_reason_count(tmp_path):
    (tmp_path / "acme.json").write_text(json.dumps({"name": "Acme", "career_url": "https://acme.com/careers", "jobs": [
        {"id": "1", "title": "Backend Engineer", "url": "https://acme.com/careers/backend-1", "description": "Requirements: Python", "status": "new",
         "job_evidence": {"jsonld_jobposting": True, "apply_cta": False, "requirement_sections": 1, "role_family_from_title": "backend", "url_shape": "acme.com|careers|2"}},
        {"id": "2", "title": "Contact sales", "url": "https://acme.com/sales/contact-us", "description": CHROME, "status": "new",
         "job_evidence": {"jsonld_jobposting": False, "apply_cta": False, "requirement_sections": 0, "role_family_from_title": None, "url_shape": "acme.com|sales|2"}},
    ]}), encoding="utf-8")
    store = MemoryPlanStore()
    store.put(_plan())
    rows = audit_scrape.audit(tmp_path, store, limit=10)
    assert rows[0]["title"] == "Contact sales" and len(rows[0]["reasons"]) >= 3
    assert all(r["title"] != "Backend Engineer" for r in rows)


def test_add_reject_pattern_appends_once(tmp_path):
    path = tmp_path / "link_rejects.json"
    assert audit_scrape.add_reject_pattern(r"^https://acme\.com/sales/", path) == [r"^https://acme\.com/sales/"]
    assert audit_scrape.add_reject_pattern(r"^https://acme\.com/sales/", path) == [r"^https://acme\.com/sales/"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"patterns": [r"^https://acme\.com/sales/"]}
