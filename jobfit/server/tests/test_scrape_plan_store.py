"""Plans on disk: one JSON per company under data/scrape_plans/, loaded
back through the pydantic model; an older schema_version comes back as
status "stale" so discovery re-derives it under the budget."""

import json
from datetime import datetime, timezone

from jobfit.scrape import ids, models
from jobfit.scrape.plan_store import FilePlanStore, MemoryPlanStore
from jobfit.scripts import update_jobs

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _plan(company_id="acme", **overrides):
    base = dict(company_id=company_id, career_url="https://acme.com/careers", derived_by="probe", derived_at=NOW,
                status="verified", strategy=models.TechmapOnlyStrategy(reason="x"))
    base.update(overrides)
    return models.ScrapePlan(**base)


def test_plan_id_matches_update_jobs_snake_case_for_real_names():
    for name in ("monday.com", "Monday.com Ltd. (Formerly DaPulse)", "Check Point", "4M Analytics", "  Wiz ", "Ré Sumé"):
        assert ids.plan_id_for(name) == update_jobs._snake_case(name)
    assert ids.plan_id_for("") == "unnamed_company"


def test_file_store_round_trips_and_lists_sorted(tmp_path):
    store = FilePlanStore(tmp_path)
    assert store.get("acme") is None
    store.put(_plan("zeta"))
    store.put(_plan("acme"))
    assert store.get("acme") == _plan("acme")
    assert [p.company_id for p in store.all()] == ["acme", "zeta"]
    assert (tmp_path / "acme.json").exists()


def test_file_store_marks_an_older_schema_version_stale(tmp_path):
    store = FilePlanStore(tmp_path)
    data = _plan("acme").model_dump(mode="json")
    data["schema_version"] = 0
    (tmp_path / "acme.json").write_text(json.dumps(data), encoding="utf-8")
    assert store.get("acme").status == "stale"


def test_file_store_returns_none_for_a_corrupt_file(tmp_path):
    (tmp_path / "acme.json").write_text("{not json", encoding="utf-8")
    assert FilePlanStore(tmp_path).get("acme") is None


def test_memory_store():
    store = MemoryPlanStore()
    store.put(_plan("acme"))
    assert store.get("acme").company_id == "acme"
    assert [p.company_id for p in store.all()] == ["acme"]
