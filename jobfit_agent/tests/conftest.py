import base64

import pytest

from jobfit.store import companies, db, jobs, scores

from jobfit_agent.agent import config

NOW = "2026-10-01T10:00:00Z"
# One seeded job carries jobfit's real id scheme (base64url of the url), so the
# url lookup has something genuine to find.
URL_JOB_URL = "https://beta.test/real-one"
URL_JOB_ID = base64.urlsafe_b64encode(URL_JOB_URL.encode()).decode().rstrip("=")


def _seed(conn):
    companies.upsert_company(conn, "acme", "Acme")
    companies.upsert_company(conn, "beta", "Beta")
    jobs.upsert_scraped(conn, "acme", [
        {"id": "j1", "title": "Senior Backend Engineer", "url": "https://acme.test/1", "city": "Tel Aviv",
         "description": "Requirements: Python and Kubernetes. Salary $150,000 - $180,000 per year."},
        {"id": "j2", "title": "Platform Engineer", "url": "https://acme.test/2", "city": "Tel Aviv",
         "description": "Requirements: Terraform"},
    ], NOW)
    jobs.upsert_scraped(conn, "beta", [
        {"id": "j3", "title": "DevOps Engineer", "url": "https://beta.test/3", "city": "Haifa",
         "description": "Requirements: Kubernetes"},
        {"id": URL_JOB_ID, "title": "SRE", "url": URL_JOB_URL, "city": "Haifa",
         "description": "Requirements: Linux"},
    ], NOW)
    scores.write_scores(conn, "j1", {"default": {"score": 90, "matched": ["python"], "cache_key": "a"}})
    scores.write_scores(conn, "j2", {"default": {"score": 70, "matched": [], "cache_key": "a"}})
    scores.write_scores(conn, "j3", {"default": {"score": 80, "matched": ["kubernetes"], "cache_key": "a"}})
    companies.refresh_connection_counts(conn, {"acme": [
        {"name": "Jane", "position": "Staff Engineer", "url": "https://linkedin.com/in/jane"}]})


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Nothing a test does may touch the real jobfit.db or the agent's real out/cache dirs, and the
    process-wide read-only connection must not leak from one test to the next."""
    from jobfit import config as jobfit_config
    from jobfit_agent.agent.tools import jobfit_store

    monkeypatch.setattr(jobfit_config, "DB_PATH", tmp_path / "no-such-real.db")
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "CHECKPOINT_DB", tmp_path / "checkpoints.sqlite")
    yield
    jobfit_store.set_conn(None)


@pytest.fixture
def store():
    from jobfit_agent.agent.tools import jobfit_store

    conn = db.connect(":memory:")
    db.migrate(conn)
    _seed(conn)
    jobfit_store.set_conn(conn)
    return conn
