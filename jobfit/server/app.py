"""FastAPI app for the jobfit control panel — localhost only, no auth."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from jobfit import company_review, config, cv
from jobfit.scripts import update_jobs
from jobfit.server import dashboard, runner, singleton_lock

STATIC_DIR = Path(__file__).parent / "static"
LOCK_PATH = config.ROOT / "data" / ".server.lock"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Two server processes writing companies/*.json and profiles.json at once
    # silently corrupt/lose data - see singleton_lock's own docstring.
    singleton_lock.acquire(LOCK_PATH)
    runner.mark_orphaned_runs_crashed()
    yield
    singleton_lock.release(LOCK_PATH)


app = FastAPI(title="jobfit control panel", lifespan=_lifespan)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "panel.html")


@app.get("/api/dashboard")
def api_dashboard() -> dict:
    return dashboard.get_dashboard_stats()


@app.get("/jobfit.html")
def output_html() -> FileResponse:
    if not config.OUTPUT_HTML.exists():
        raise HTTPException(404, "jobfit.html hasn't been generated yet - run an update first")
    return FileResponse(config.OUTPUT_HTML)


@app.get("/api/profiles")
def api_list_profiles() -> list[dict]:
    return [{"id": pid, **entry} for pid, entry in cv.load_registry().items()]


CV_UPLOAD_EXTENSIONS = (".docx", ".pdf")


@app.post("/api/profiles")
async def api_add_profile(name: str = Form(...), file: UploadFile = File(...)) -> dict:
    filename = (file.filename or "").lower()
    if not filename.endswith(CV_UPLOAD_EXTENSIONS):
        raise HTTPException(400, "CV must be a .docx or .pdf file")
    config.CV_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = config.CV_PROFILES_DIR / f"_upload_{file.filename}"
    tmp_path.write_bytes(await file.read())
    try:
        profile_id = cv.register_profile(name, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"id": profile_id, **dashboard.get_dashboard_stats()}


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: str) -> dict:
    cv.remove_profile(profile_id)
    return dashboard.get_dashboard_stats()


@app.post("/api/connections")
async def api_upload_connections(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Connections export must be a .csv file")
    config.CONNECTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    config.CONNECTIONS_CSV.write_bytes(await file.read())
    return dashboard.get_dashboard_stats()


@app.get("/api/referrals")
def api_list_referrals() -> list[dict]:
    if not config.REFERRAL_UPLOADS_DIR.exists():
        return []
    entries = []
    for path in config.REFERRAL_UPLOADS_DIR.glob("*.json"):
        timestamp, _, original_name = path.stem.partition("-")
        try:
            uploaded_at = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            uploaded_at = None
        entries.append({"filename": original_name or path.name, "uploaded_at": uploaded_at})
    entries.sort(key=lambda e: e["uploaded_at"] or "", reverse=True)
    return entries


@app.post("/api/referrals")
async def api_upload_referral(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".json"):
        raise HTTPException(400, "Referral export must be a .json file")
    raw = await file.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Not valid JSON")
    if "companies" not in payload:
        raise HTTPException(400, 'Expected a top-level "companies" key')

    config.REFERRAL_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = config.REFERRAL_UPLOADS_DIR / f"{timestamp}-{file.filename}"
    archive_path.write_bytes(raw)

    profiles = cv.load_profiles()
    stats = update_jobs.merge_referral_jobs(profiles, path=archive_path)
    return {**stats, **dashboard.get_dashboard_stats()}


@app.get("/api/companies/needs-review")
def api_companies_needs_review() -> list[dict]:
    techmap_index = update_jobs.load_techmap_index()
    return company_review.companies_needing_review(techmap_index)


@app.post("/api/companies/{company}/decision")
def api_set_company_decision(company: str, payload: dict) -> dict:
    decision = payload.get("decision", "")
    try:
        company_review.set_decision(company, decision)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except KeyError:
        raise HTTPException(404, f"unknown company {company!r}")
    return {"company": company, "decision": decision}


@app.post("/api/companies/{company}/career-url")
def api_set_company_career_url(company: str, payload: dict) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    try:
        company_review.set_career_url(company, url)
    except KeyError:
        raise HTTPException(404, f"unknown company {company!r}")
    return {"company": company, "url": url}


@app.post("/api/run")
def api_start_run(payload: dict) -> dict:
    force = bool(payload.get("force", False))
    companies = payload.get("companies")
    try:
        run_id = runner.start_run(force, companies=companies)
    except RuntimeError as error:
        raise HTTPException(409, str(error))
    return {"run_id": run_id, "status": "started"}


@app.get("/api/run/status")
def api_run_status() -> dict:
    return runner.status()


@app.get("/api/run/history")
def api_run_history() -> list[dict]:
    return runner.get_history()


@app.get("/api/run/stream")
def api_run_stream() -> StreamingResponse:
    line_queue = runner.log_queue()
    if line_queue is None:
        def _idle():
            yield "event: idle\ndata: no run active\n\n"
        return StreamingResponse(_idle(), media_type="text/event-stream")

    def _stream():
        while True:
            line = line_queue.get()
            if line is None:
                yield "event: done\ndata: run finished\n\n"
                break
            yield f"data: {line}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")
