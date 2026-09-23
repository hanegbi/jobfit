import json

from jobfit import config, cv
from jobfit.server import dashboard


def test_dashboard_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")

    (tmp_path / "companies").mkdir()
    (tmp_path / "companies" / "wiz.json").write_text("{}", encoding="utf-8")
    (tmp_path / "companies" / "_meta.json").write_text("{}", encoding="utf-8")

    cv.save_registry({"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}})
    config.JOBS_OUTPUT_JSON.write_text(json.dumps([
        {"status": "seen", "score_default": 72},
        {"status": "new", "score_default": 45},
        {"status": "closed", "score_default": 90},
    ]), encoding="utf-8")

    stats = dashboard.get_dashboard_stats()

    assert stats["total_jobs_open"] == 2
    assert stats["total_jobs_all_time"] == 3
    assert stats["companies"] == 1
    assert stats["connections"] == 0
    assert stats["profiles"] == [{"id": "default", "name": "Default"}]
    assert stats["score_distribution"] == {"default": {40: 1, 70: 1}}


def test_dashboard_stats_with_no_data_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")

    stats = dashboard.get_dashboard_stats()

    assert stats == {
        "total_jobs_open": 0, "total_jobs_all_time": 0, "companies": 0,
        "connections": 0, "profiles": [], "score_distribution": {},
    }
