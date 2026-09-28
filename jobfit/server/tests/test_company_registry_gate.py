"""save_company_file is the single place every new companies/*.json file
gets created (directly, and via merge_referral_jobs -> load_company_file
+ save_company_file). Gating it here - rather than each caller
separately - means merge_referral_jobs benefits from host-based and
loose-key matching too, even though its own inline matching (existing
code, unchanged by this plan) only ever checked normalized names."""

import json

import pytest

from jobfit import company_registry
from jobfit.scripts import update_jobs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    company_registry._registry_cache.clear()
    return companies_dir


def _write_company(companies_dir, stem, name, career_url=None):
    (companies_dir / f"{stem}.json").write_text(
        json.dumps({"name": name, "career_url": career_url, "last_checked": None, "jobs": []}),
        encoding="utf-8",
    )


def test_save_company_file_allows_updating_an_existing_company(isolated):
    # The file's own stem must match _snake_case(display_name) for this to
    # exercise the "already exists" path rather than accidentally look like
    # a brand new company with a colliding name (that's a different test).
    _write_company(isolated, "acme_corp", "Acme Corp", "https://acme.com/careers")

    update_jobs.save_company_file("Acme Corp", {"name": "Acme Corp", "career_url": "https://acme.com/careers", "jobs": []})

    assert (isolated / "acme_corp.json").exists()


def test_save_company_file_allows_a_genuinely_new_company(isolated):
    update_jobs.save_company_file("Brand New Co", {"name": "Brand New Co", "career_url": None, "jobs": []})

    assert (isolated / "brand_new_co.json").exists()


def test_save_company_file_raises_for_a_new_spelling_of_an_existing_host(isolated):
    _write_company(isolated, "mondaycom", "monday.com", "https://monday.com/careers")

    with pytest.raises(company_registry.DuplicateCompany) as excinfo:
        update_jobs.save_company_file("Monday.com Ltd. (Formerly DaPulse)", {
            "name": "Monday.com Ltd. (Formerly DaPulse)", "career_url": "https://monday.com/careers", "jobs": [],
        })
    assert excinfo.value.conflict_id == "mondaycom"
    assert not (isolated / "mondaycom_ltd_formerly_dapulse.json").exists()


def test_save_company_file_raises_for_a_new_spelling_matching_only_by_normalized_name(isolated):
    _write_company(isolated, "apiiro", "Apiiro", None)

    with pytest.raises(company_registry.DuplicateCompany):
        update_jobs.save_company_file("Apiiro Ltd.", {"name": "Apiiro Ltd.", "career_url": None, "jobs": []})
    assert not (isolated / "apiiro_ltd.json").exists()
