"""FastAPI app for the jobfit control panel — localhost only, no auth."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from jobfit import company_review, config, cv, pipeline_lock
from jobfit.scripts import update_jobs
from jobfit.server import dashboard, runner
from jobfit.store import db, facets, search
from jobfit.store import jobs as store_jobs
from jobfit.store import state as store_state

STATIC_DIR = Path(__file__).parent / "static"
LOCK_PATH = config.ROOT / "data" / ".server.lock"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Two server processes writing companies/*.json and profiles.json at once
    # silently corrupt/lose data - see pipeline_lock's own docstring. This
    # reuses the same lock mechanism as the pipeline stages, at a different
    # path and for a different purpose (one whole server instance, for its
    # entire lifetime, rather than one mutating stage at a time).
    server_lock = pipeline_lock.PipelineLock(LOCK_PATH, stage="server", scope="instance")
    server_lock.__enter__()
    runner.mark_orphaned_runs_crashed()
    yield
    server_lock.__exit__(None, None, None)


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


# --- the jobs API: what a front end reads ---------------------------------

@app.get("/api/jobs")
def api_jobs(
    q: str | None = None, company: str | None = None, city: str | None = None,
    status: str | None = None, remote: bool | None = None, min_score: float | None = None,
    profile: str = "best", liked: bool | None = None, hidden: bool | None = None,
    sent: bool | None = None, has_connection: bool | None = None,
    sort: str = "score", page: int = 1, size: int = 50,
) -> dict:
    """One page of matching jobs plus the full total. List rows carry no
    description, and size is capped: an unbounded page would let one request
    pull the whole dataset, which is what this API exists to avoid."""
    return search.search_jobs(
        db.shared(), q=q, company_id=company, city=city, status=status, is_remote=remote,
        min_score=min_score, profile=profile, liked=liked, hidden=hidden, sent=sent,
        has_connection=has_connection, sort=sort, page=page, size=min(max(1, size), 500),
    )


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str) -> dict:
    job = store_jobs.detail(db.shared(), job_id)
    if job is None:
        raise HTTPException(404, f"unknown job {job_id!r}")
    return job


@app.patch("/api/jobs/{job_id}/state")
def api_set_job_state(job_id: str, payload: dict) -> dict:
    """Record what the user thinks of a job: liked, hidden, sent, reached out.
    Returns the whole new state, so a client never has to guess."""
    try:
        return store_state.set_state(db.shared(), job_id, **payload)
    except KeyError:
        raise HTTPException(404, f"unknown job {job_id!r}")
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get("/api/facets")
def api_facets(
    q: str | None = None, company: str | None = None, city: str | None = None,
    status: str | None = None, remote: bool | None = None, min_score: float | None = None,
    profile: str = "best", liked: bool | None = None, hidden: bool | None = None,
    sent: bool | None = None, has_connection: bool | None = None,
) -> dict:
    """Counts per company, city and status for the current filter - built from
    the same WHERE clause as /api/jobs, so they cannot disagree."""
    return facets.counts(
        db.shared(), q=q, company_id=company, city=city, status=status, is_remote=remote,
        min_score=min_score, profile=profile, liked=liked, hidden=hidden, sent=sent,
        has_connection=has_connection,
    )


@app.get("/api/companies")
def api_companies() -> list[dict]:
    return facets.companies(db.shared())


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


@app.post("/api/run/stop")
def api_stop_run() -> dict:
    if not runner.stop_run():
        raise HTTPException(409, "no run is active")
    return {"status": "stopping"}


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
