import json

from jobfit import config, cv
from jobfit.server import dashboard
from jobfit.store import companies as store_companies
from jobfit.store import jobs as store_jobs
from jobfit.store import scores as store_scores


def test_dashboard_stats(tmp_path, monkeypatch, store_conn):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")

    cv.save_registry({"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}})
    store_companies.upsert_company(store_conn, "wiz", "Wiz")
    store_jobs.upsert_scraped(store_conn, "wiz", [
        {"id": "a", "title": "Open One", "url": "u1"},
        {"id": "b", "title": "Open Two", "url": "u2"},
        {"id": "c", "title": "Gone", "url": "u3"},
    ], "2026-09-30T10:00:00Z")
    store_conn.execute("UPDATE jobs SET status = 'closed' WHERE id = 'c'")
    store_scores.write_scores(store_conn, "a", {"default": {"score": 72, "cache_key": "k"}})
    store_scores.write_scores(store_conn, "b", {"default": {"score": 45, "cache_key": "k"}})
    # A closed job's score must not appear in the distribution.
    store_scores.write_scores(store_conn, "c", {"default": {"score": 90, "cache_key": "k"}})

    stats = dashboard.get_dashboard_stats()

    assert stats["total_jobs_open"] == 2
    assert stats["total_jobs_all_time"] == 3
    assert stats["companies"] == 1
    assert stats["connections"] == 0
    assert stats["profiles"] == [{"id": "default", "name": "Default", "uploaded_at": "x"}]
    assert stats["score_distribution"] == {"default": {40: 1, 70: 1}}
    assert stats["connections_uploaded_at"] is None
    assert stats["html_updated_at"] is None


def test_dashboard_stats_with_no_data_yet(tmp_path, monkeypatch, store_conn):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")

    stats = dashboard.get_dashboard_stats()

    assert stats == {
        "total_jobs_open": 0, "total_jobs_all_time": 0, "companies": 0,
        "connections": 0, "connections_uploaded_at": None, "html_updated_at": None,
        "profiles": [], "score_distribution": {},
    }


def test_connections_uploaded_at_reflects_the_file_mtime(tmp_path, monkeypatch, store_conn):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    config.CONNECTIONS_CSV.write_text("First Name,Last Name,Company\n", encoding="utf-8")

    stats = dashboard.get_dashboard_stats()

    assert stats["connections_uploaded_at"] is not None


def test_html_updated_at_reflects_the_output_file_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    config.OUTPUT_HTML.write_text("<html></html>", encoding="utf-8")

    stats = dashboard.get_dashboard_stats()

    assert stats["html_updated_at"] is not None
