"""End-to-end HTTP-layer tests for the control panel's FastAPI routes.

Unlike the rest of jobfit/server/tests/, which test individual functions in
isolation, this exercises the actual routes through FastAPI's TestClient -
real request in, real response out. This is the layer that a real data-loss
bug (two server processes racing on companies/*.json and profiles.json, see
the singleton_lock commit) went undetected in for a whole session, because
every earlier verification was manual (curl/browser), never repeatable.

recompute_stage() itself is mocked in every test that triggers it - a real
call touches techmap's cache and does a full rescore, which is slow and not
what these tests are about. logging_stream/runner's own background-thread
behavior already has dedicated unit tests (test_runner.py); here it's enough
to confirm the HTTP layer wires up to it correctly.
"""

import json
from pathlib import Path

import docx as docx_lib
import pytest
from fastapi.testclient import TestClient

from jobfit import config
from jobfit.scripts import update_jobs
from jobfit.server import app as app_module
from jobfit.server import runner


def _make_docx_bytes(text: str = "Python engineer with Kubernetes experience.") -> bytes:
    import io
    document = docx_lib.Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _make_pdf_bytes(text: str = "Python engineer with Kubernetes experience.") -> bytes:
    content = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 300 144] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_DIR", tmp_path / "cvs")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "REFERRAL_UPLOADS_DIR", tmp_path / "referrals")
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    # Module-level constants computed at import time from config.ROOT - patching
    # config.ROOT alone doesn't reach these (see update_jobs.py:42-43).
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path / "companies")
    monkeypatch.setattr(update_jobs, "META_PATH", tmp_path / "companies" / "_meta.json")
    monkeypatch.setattr(app_module, "LOCK_PATH", tmp_path / ".server.lock")

    (tmp_path / "companies").mkdir()
    (tmp_path / "companies_career_pages.json").write_text("{}", encoding="utf-8")

    # Ensure no state leaks in from a previous test via the module-level singleton.
    with runner._lock:
        runner._state.update(running=False, run_id=None, started_at=None, queue=None)

    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def no_op_recompute(monkeypatch):
    """recompute_stage() is what runner.start_recompute() calls in the
    background - stub it so upload tests don't touch real techmap/scoring."""
    calls = {"n": 0}
    monkeypatch.setattr(update_jobs, "recompute_stage", lambda: calls.__setitem__("n", calls["n"] + 1))
    return calls


def _wait_for_idle(timeout_s: float = 2.0) -> None:
    import time
    deadline = time.time() + timeout_s
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.02)


# --- basic pages -------------------------------------------------------

def test_index_serves_the_panel_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "jobfit control panel" in res.text


def test_dashboard_with_no_data_yet(client):
    res = client.get("/api/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["total_jobs_open"] == 0
    assert body["companies"] == 0
    assert body["profiles"] == []


# --- profiles ------------------------------------------------------------

def test_list_profiles_starts_empty(client):
    assert client.get("/api/profiles").json() == []


def test_add_profile_with_docx_returns_immediately_and_triggers_recompute(client, no_op_recompute):
    res = client.post(
        "/api/profiles",
        data={"name": "Default"},
        files={"file": ("resume.docx", _make_docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "default"
    assert body["recompute_pending"] is True

    _wait_for_idle()
    assert no_op_recompute["n"] == 1

    profiles = client.get("/api/profiles").json()
    assert [p["id"] for p in profiles] == ["default"]


def test_add_profile_with_pdf_is_accepted(client, no_op_recompute):
    res = client.post(
        "/api/profiles",
        data={"name": "PDF Profile"},
        files={"file": ("resume.pdf", _make_pdf_bytes(), "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["id"] == "pdf_profile"
    _wait_for_idle()


def test_add_profile_rejects_an_unsupported_extension(client, no_op_recompute):
    res = client.post(
        "/api/profiles",
        data={"name": "Bad"},
        files={"file": ("resume.txt", b"plain text resume", "text/plain")},
    )
    assert res.status_code == 400
    assert no_op_recompute["n"] == 0
    assert client.get("/api/profiles").json() == []


def test_delete_profile_removes_it_and_triggers_recompute(client, no_op_recompute):
    client.post(
        "/api/profiles", data={"name": "Default"},
        files={"file": ("resume.docx", _make_docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    _wait_for_idle()
    no_op_recompute["n"] = 0

    res = client.delete("/api/profiles/default")
    assert res.status_code == 200
    assert res.json()["recompute_pending"] is True
    _wait_for_idle()
    assert no_op_recompute["n"] == 1
    assert client.get("/api/profiles").json() == []


def test_delete_unknown_profile_is_a_no_op(client, no_op_recompute):
    res = client.delete("/api/profiles/does-not-exist")
    assert res.status_code == 200
    _wait_for_idle()


# --- connections -----------------------------------------------------------

def test_upload_connections_accepts_a_csv(client, no_op_recompute):
    csv_bytes = b"First Name,Last Name,Company,Position,URL\nJane,Doe,Acme,Engineer,https://x\n"
    res = client.post("/api/connections", files={"file": ("Connections.csv", csv_bytes, "text/csv")})
    assert res.status_code == 200
    assert res.json()["recompute_pending"] is True
    assert config.CONNECTIONS_CSV.read_bytes() == csv_bytes
    _wait_for_idle()
    assert no_op_recompute["n"] == 1


def test_upload_connections_rejects_a_non_csv_file(client, no_op_recompute):
    res = client.post("/api/connections", files={"file": ("Connections.json", b"{}", "application/json")})
    assert res.status_code == 400
    assert no_op_recompute["n"] == 0
    assert not config.CONNECTIONS_CSV.exists()


# --- referrals ---------------------------------------------------------

def test_list_referrals_starts_empty(client):
    assert client.get("/api/referrals").json() == []


def test_upload_referral_merges_and_archives_it(client, no_op_recompute):
    payload = {
        "companies": [{
            "company": "Acme",
            "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe", "requirements": ["python"]}],
        }],
    }
    res = client.post(
        "/api/referrals",
        files={"file": ("export.json", json.dumps(payload).encode(), "application/json")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["added_new_job"] == 1
    assert body["recompute_pending"] is True
    _wait_for_idle()
    assert no_op_recompute["n"] == 1

    saved = json.loads((update_jobs.COMPANIES_DIR / "acme.json").read_text(encoding="utf-8"))
    assert saved["jobs"][0]["title"] == "Backend Engineer"

    listed = client.get("/api/referrals").json()
    assert len(listed) == 1
    # api_list_referrals derives this from path.stem (already .json-stripped)
    # partitioned on "-", so the extension isn't part of the display name -
    # the archived file on disk still keeps the real "export.json" name.
    assert listed[0]["filename"] == "export"
    assert listed[0]["uploaded_at"] is not None


def test_upload_referral_rejects_invalid_json(client, no_op_recompute):
    res = client.post("/api/referrals", files={"file": ("export.json", b"not json", "application/json")})
    assert res.status_code == 400
    assert no_op_recompute["n"] == 0


def test_upload_referral_rejects_json_missing_companies_key(client, no_op_recompute):
    res = client.post("/api/referrals", files={"file": ("export.json", b'{"foo": []}', "application/json")})
    assert res.status_code == 400
    assert no_op_recompute["n"] == 0


def test_upload_referral_rejects_a_non_json_file(client, no_op_recompute):
    res = client.post("/api/referrals", files={"file": ("export.csv", b"a,b,c", "text/csv")})
    assert res.status_code == 400
    assert no_op_recompute["n"] == 0


# --- run trigger ---------------------------------------------------------

def test_run_status_is_idle_by_default(client):
    assert client.get("/api/run/status").json()["running"] is False


def test_run_history_starts_empty(client):
    assert client.get("/api/run/history").json() == []


def test_start_run_returns_a_run_id_and_marks_running(client, monkeypatch):
    def _fake_scrape_stage(companies, profiles, force=False):
        return update_jobs.RunStats(companies_checked=0, companies_skipped=0, new_jobs=0, closed_jobs=0, failures=[])
    monkeypatch.setattr(update_jobs, "scrape_stage", _fake_scrape_stage)
    monkeypatch.setattr(update_jobs, "recompute_stage", lambda: None)
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})

    res = client.post("/api/run", json={"force": False})
    assert res.status_code == 200
    assert res.json()["run_id"]
    _wait_for_idle()

    history = client.get("/api/run/history").json()
    assert len(history) == 1
    assert history[0]["finished_at"] is not None


def test_start_run_returns_409_when_already_running(client):
    with runner._lock:
        runner._state.update(running=True, run_id="already-running", started_at="x", queue=None)
    try:
        res = client.post("/api/run", json={"force": False})
        assert res.status_code == 409
    finally:
        with runner._lock:
            runner._state.update(running=False, run_id=None, started_at=None, queue=None)


def test_run_stream_reports_idle_when_no_run_is_active(client):
    with client.stream("GET", "/api/run/stream") as res:
        assert res.status_code == 200
        lines = list(res.iter_lines())
    assert any("idle" in line for line in lines)
