"""CompanyRegistry gives every company exactly one id, derived by one of
four resolution tiers (exact normalized name, career-page host, a looser
name key that also strips parentheticals and .com/.io/.ai/.co, or - not
implemented until the full migration - a hand-reviewed alias). Measured
live on the real data (2026-09-28): 111 groups of company files already
collapse under the normalized-name key alone, 168 under the loose key,
174 by shared career-page host - this class is what a future migration
uses to actually merge them; for now (this plan) it only prevents NEW
collisions via register_if_new, seeded read-only from whatever already
exists on disk."""

import json

import pytest

from jobfit import company_registry


@pytest.fixture
def companies_dir(tmp_path):
    d = tmp_path / "companies"
    d.mkdir()
    return d


def _write_company(companies_dir, stem, name, career_url=None):
    (companies_dir / f"{stem}.json").write_text(
        json.dumps({"name": name, "career_url": career_url, "last_checked": None, "jobs": []}),
        encoding="utf-8",
    )


def test_load_seeds_one_entry_per_existing_company_file(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    _write_company(companies_dir, "wiz", "Wiz", "https://wiz.io/careers")

    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Acme Corp") == "acme"
    assert registry.resolve("Wiz") == "wiz"


def test_load_persists_the_seed_so_a_second_load_does_not_reread_company_files(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    registry_path = tmp_path / "registry.json"

    company_registry.CompanyRegistry.load(registry_path, companies_dir)
    assert registry_path.exists()

    # Remove the company file; a second load must still know about "acme"
    # because it now reads from the persisted registry, not the (now-empty)
    # companies_dir.
    (companies_dir / "acme.json").unlink()
    registry = company_registry.CompanyRegistry.load(registry_path, companies_dir)
    assert registry.resolve("Acme Corp") == "acme"


def test_resolve_matches_by_normalized_name(companies_dir, tmp_path):
    _write_company(companies_dir, "apiiro", "Apiiro", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Apiiro Ltd.") == "apiiro"  # "Ltd." stripped by normalize_company


def test_resolve_matches_by_career_url_host(companies_dir, tmp_path):
    _write_company(companies_dir, "mondaycom", "monday.com", "https://monday.com/careers")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Monday.com Ltd. (Formerly DaPulse)", "https://monday.com/careers/some-job") == "mondaycom"


def test_resolve_matches_by_loose_key_stripping_parentheticals(companies_dir, tmp_path):
    _write_company(companies_dir, "mondaycom", "monday.com", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Monday.com Ltd. (Formerly DaPulse)") == "mondaycom"


def test_resolve_returns_none_for_a_genuinely_new_company(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Totally Different Co", "https://totallydifferent.example/careers") is None


def test_register_if_new_adds_a_genuinely_new_company(companies_dir, tmp_path):
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    conflict = registry.register_if_new("newco", "New Co", "https://newco.example/careers")

    assert conflict is None
    assert registry.resolve("New Co") == "newco"


def test_register_if_new_returns_the_conflicting_id_without_registering(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    conflict = registry.register_if_new("acme_corp_ltd", "Acme Corp Ltd.", "https://acme.com/careers/jobs")

    assert conflict == "acme"
    assert registry.resolve("Acme Corp Ltd.") == "acme"  # still resolves via normalize_company, no new entry


def test_register_if_new_is_a_noop_when_the_id_is_already_registered(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    conflict = registry.register_if_new("acme", "Acme Corp", None)

    assert conflict is None  # updating the existing entry, not creating a new one


def test_check_invariants_is_empty_for_a_clean_registry(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    _write_company(companies_dir, "wiz", "Wiz", "https://wiz.io/careers")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.check_invariants() == []


def test_check_invariants_reports_a_shared_host(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    _write_company(companies_dir, "acme_other", "Acme Other Spelling", "https://acme.com/jobs")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    problems = registry.check_invariants()

    assert len(problems) == 1
    assert "acme" in problems[0] and "acme_other" in problems[0]


def test_get_registry_caches_by_registry_path(tmp_path, companies_dir):
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"

    reg_a1 = company_registry.get_registry(companies_dir, path_a)
    reg_a2 = company_registry.get_registry(companies_dir, path_a)
    reg_b = company_registry.get_registry(companies_dir, path_b)

    assert reg_a1 is reg_a2  # same path -> cached instance
    assert reg_a1 is not reg_b  # different path -> independent instance
