import json

import pytest

from jobfit import company_review, config


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")


def _write_pages(pages: dict) -> None:
    config.COMPANIES_CAREER_PAGES_PATH.write_text(json.dumps(pages), encoding="utf-8")


def test_load_career_pages_returns_empty_dict_when_file_missing():
    assert company_review.load_career_pages() == {}


def test_load_review_returns_empty_dict_when_file_missing():
    assert company_review.load_review() == {}


def test_set_decision_persists_techmap_approval():
    _write_pages({"Acme": None})
    company_review.set_decision("Acme", "techmap")
    review = company_review.load_review()
    assert review["Acme"]["decision"] == "techmap"
    assert "decided_at" in review["Acme"]


def test_set_decision_persists_skip():
    _write_pages({"Acme": None})
    company_review.set_decision("Acme", "skip")
    assert company_review.load_review()["Acme"]["decision"] == "skip"


def test_set_decision_rejects_unknown_decision():
    _write_pages({"Acme": None})
    with pytest.raises(ValueError):
        company_review.set_decision("Acme", "maybe")


def test_set_decision_rejects_unknown_company():
    _write_pages({"Acme": None})
    with pytest.raises(KeyError):
        company_review.set_decision("Nope", "techmap")


def test_set_career_url_updates_career_pages():
    _write_pages({"Acme": None})
    company_review.set_career_url("Acme", "https://acme.com/careers")
    assert company_review.load_career_pages()["Acme"] == "https://acme.com/careers"


def test_set_career_url_clears_any_prior_review_decision():
    _write_pages({"Acme": None})
    company_review.set_decision("Acme", "skip")
    company_review.set_career_url("Acme", "https://acme.com/careers")
    assert "Acme" not in company_review.load_review()


def test_set_career_url_rejects_unknown_company():
    _write_pages({"Acme": None})
    with pytest.raises(KeyError):
        company_review.set_career_url("Nope", "https://nope.com")


def test_companies_needing_review_excludes_companies_with_a_url():
    _write_pages({"Acme": "https://acme.com/careers", "Beta": None})
    results = company_review.companies_needing_review({})
    assert [r["company"] for r in results] == ["Beta"]


def test_companies_needing_review_reports_pending_by_default():
    _write_pages({"Beta": None})
    results = company_review.companies_needing_review({})
    assert results[0]["decision"] == "pending"
    assert results[0]["has_techmap"] is False


def test_companies_needing_review_reports_decided_status():
    _write_pages({"Beta": None})
    company_review.set_decision("Beta", "skip")
    results = company_review.companies_needing_review({})
    assert results[0]["decision"] == "skip"


def test_companies_needing_review_reports_techmap_availability():
    _write_pages({"Beta": None})
    techmap_index = {"beta": [{"title": "Backend Engineer", "location": "Remote", "url": "https://x"}]}
    results = company_review.companies_needing_review(techmap_index)
    assert results[0]["has_techmap"] is True
    assert results[0]["techmap_job_count"] == 1
    assert results[0]["techmap_sample_title"] == "Backend Engineer"


def test_companies_needing_review_sorts_pending_with_techmap_data_first():
    _write_pages({"NoData": None, "HasData": None, "Decided": None})
    company_review.set_decision("Decided", "skip")
    techmap_index = {"hasdata": [{"title": "Engineer", "location": None, "url": "https://x"}]}
    results = company_review.companies_needing_review(techmap_index)
    assert [r["company"] for r in results] == ["HasData", "NoData", "Decided"]
