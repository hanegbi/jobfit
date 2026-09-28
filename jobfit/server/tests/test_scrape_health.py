"""HealthPolicy: whether a scrape result looks real (never consulting CV
score), and the plan status transitions of spec section 4.4."""

from datetime import datetime, timezone

from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.models import Evidence, JobPosting, PageFingerprint, PlanHealth, ScrapePlan, TechmapOnlyStrategy

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _plan(status="verified", baseline=5, fingerprint="abc"):
    return ScrapePlan(
        company_id="acme", career_url="https://acme.com/careers", derived_by="rules", derived_at=NOW,
        verified_at=NOW if status == "verified" else None, status=status,
        strategy=TechmapOnlyStrategy(reason="x"), health=PlanHealth(baseline_yield=baseline),
        page_fingerprint=PageFingerprint(href_shape_set_hash=fingerprint, candidate_count=10),
    )


def _p(source="html_listing", evidence=None, url="https://acme.com/x"):
    return JobPosting(title="Backend Engineer", url=url, source=source, evidence=evidence)


def test_empty_is_unhealthy_and_trusted_sources_are_healthy():
    policy = HealthPolicy()
    assert policy.is_healthy([]) is False
    for source in ("ats_api", "external_board", "special_case", "techmap"):
        assert policy.is_healthy([_p(source=source)]) is True


def test_html_listing_is_healthy_with_enough_evidence_or_job_shaped_urls():
    policy = HealthPolicy()
    strong = [_p(evidence=Evidence(jsonld_jobposting=True)), _p(evidence=Evidence()), _p(evidence=Evidence())]
    assert policy.is_healthy(strong) is True  # 1 of 3 >= 30%
    hinted = [_p(url="https://acme.com/careers/job-123", evidence=Evidence()), _p(url="https://acme.com/about", evidence=Evidence()), _p(url="https://acme.com/pricing", evidence=Evidence())]
    assert policy.is_healthy(hinted) is True
    marketing = [_p(url="https://acme.com/about", evidence=Evidence()), _p(url="https://acme.com/pricing", evidence=None)]
    assert policy.is_healthy(marketing) is False


def test_healthy_run_resets_counters_and_clears_stale_suspect():
    """A plan that WAS verified before going stale_suspect (a transient
    blip) recovers back to verified, not unverified - per spec 4.4,
    recovery restores the pre-suspect value via verified_at, so this
    fixture must set verified_at to actually exercise that path."""
    policy = HealthPolicy()
    plan = _plan(status="stale_suspect").model_copy(update={
        "verified_at": NOW, "health": PlanHealth(baseline_yield=5, consecutive_empty_runs=2),
    })
    updated = policy.update(plan, [_p(source="techmap")], NOW)
    assert updated.status == "verified"
    assert updated.health.consecutive_empty_runs == 0
    assert updated.health.last_ok_run == NOW and updated.health.last_run == NOW and updated.health.last_yield == 1


def test_two_consecutive_empty_runs_mark_stale_suspect_only_when_there_was_a_baseline():
    policy = HealthPolicy()
    first = policy.update(_plan(), [], NOW)
    assert first.status == "verified" and first.health.consecutive_empty_runs == 1
    second = policy.update(first, [], NOW)
    assert second.status == "stale_suspect"
    never = policy.update(_plan(baseline=0), [], NOW)
    assert never.status == "verified" and never.health.consecutive_empty_runs == 0


def test_yield_drop_with_fingerprint_drift_marks_stale_suspect_but_drift_alone_does_not():
    policy = HealthPolicy()
    plan = _plan(baseline=10)
    one = [_p(source="techmap")]
    drifted = PageFingerprint(href_shape_set_hash="zzz", candidate_count=3)
    assert policy.update(plan, one, NOW, fingerprint=drifted).status == "stale_suspect"
    same_yield = [_p(source="techmap") for _ in range(10)]
    assert policy.update(plan, same_yield, NOW, fingerprint=drifted).status == "verified"
    assert policy.update(plan, one, NOW, fingerprint=PageFingerprint(href_shape_set_hash="abc", candidate_count=10)).status == "verified"


def test_stale_schema_status_is_left_alone():
    policy = HealthPolicy()
    assert policy.update(_plan(status="stale"), [_p(source="techmap")], NOW).status == "stale"
