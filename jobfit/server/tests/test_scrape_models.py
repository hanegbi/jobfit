"""The scrape package's data models: plain pydantic, round-trippable
through JSON (plans are stored on disk as JSON and loaded back), with a
discriminated union for the per-company strategy so a plan file's
"kind" field alone picks the right model."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jobfit.scrape import models


def test_strategy_union_round_trips_each_kind_through_json():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    strategies = [
        models.AtsApiStrategy(provider="greenhouse", board="acme", board_url="https://boards.greenhouse.io/acme"),
        models.ExternalBoardStrategy(board_url="https://jobs.lever.co/acme"),
        models.HtmlListingStrategy(renderer="http", include_url=r"^https://acme\.com/careers/[a-z0-9-]+$"),
        models.SpecialCaseStrategy(host_fragment="elbitsystemscareer.com"),
        models.TechmapOnlyStrategy(reason="no career url"),
        models.BrokenUrlStrategy(reason="http 404"),
    ]
    for strategy in strategies:
        plan = models.ScrapePlan(
            company_id="acme", career_url="https://acme.com/careers", derived_by="probe",
            derived_at=now, status="verified", strategy=strategy,
        )
        dumped = json.dumps(plan.model_dump(mode="json"))
        loaded = models.ScrapePlan.model_validate(json.loads(dumped))
        assert loaded.strategy == strategy
        assert loaded.strategy.kind == strategy.kind


def test_unknown_strategy_kind_is_rejected():
    with pytest.raises(ValidationError):
        models.ScrapePlan.model_validate({
            "company_id": "acme", "career_url": None, "derived_by": "probe",
            "derived_at": "2026-09-28T00:00:00Z", "status": "verified",
            "strategy": {"kind": "magic"},
        })


def test_scrape_plan_defaults():
    plan = models.ScrapePlan(
        company_id="acme", career_url=None, derived_by="probe",
        derived_at=datetime(2026, 9, 28, tzinfo=timezone.utc), status="verified",
        strategy=models.TechmapOnlyStrategy(reason="no career url"),
    )
    assert plan.schema_version == models.SCRAPE_PLAN_SCHEMA_VERSION == 1
    assert plan.health.consecutive_empty_runs == 0
    assert plan.labels is None
    assert plan.notes == []
    assert plan.rediscover_after is None


def test_job_posting_defaults_and_evidence():
    posting = models.JobPosting(title="Backend Engineer", url="https://acme.com/careers/1", source="html_listing")
    assert posting.description == ""
    assert posting.evidence is None
    evidence = models.Evidence(jsonld_jobposting=True, url_shape="acme.com|careers|2")
    assert evidence.apply_cta is False
    assert evidence.requirement_sections == 0


def test_labels_require_a_page_verdict_from_the_fixed_set():
    with pytest.raises(ValidationError):
        models.Labels(page_verdict="maybe", candidates=[])
    labels = models.Labels(page_verdict="careers_page", candidates=[models.CandidateLabel(index=0, is_job=True, reason="ok")])
    assert labels.container_selector is None
