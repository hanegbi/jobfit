"""FastAPI app for the jobfit control panel — localhost only, no auth."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, ORJSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from jobfit import config, cv, pipeline_lock
from jobfit.scripts import update_jobs
from jobfit.server import dashboard, runner
from jobfit.store import companies as store_companies
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


APP_DIR = STATIC_DIR / "app"

# The built front end is committed, so a fresh clone serves the app without
# Node installed. Mounted rather than routed one-file-at-a-time because Vite
# emits hashed asset names.
if (APP_DIR / "assets").exists():
    app.mount("/app/assets", StaticFiles(directory=APP_DIR / "assets"), name="app-assets")


@app.get("/app")
def spa_index() -> FileResponse:
    index = APP_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "the front end is not built - run `npm run build` in frontend/")
    return FileResponse(index)


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
    return store_companies.needing_review(db.shared(), update_jobs.load_techmap_index())


@app.post("/api/companies/{company}/decision")
def api_set_company_decision(company: str, payload: dict) -> dict:
    decision = payload.get("decision", "")
    try:
        store_companies.set_decision(db.shared(), company, decision)
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
        store_companies.set_career_url(db.shared(), company, url)
    except KeyError:
        raise HTTPException(404, f"unknown company {company!r}")
    return {"company": company, "url": url}


# --- the jobs API: what a front end reads ---------------------------------

@app.get("/api/jobs", response_class=ORJSONResponse)
def api_jobs(
    q: str | None = None, scope: str = "all", exclude: str | None = None,
    company: str | None = None, city: str | None = None, status: str | None = None,
    remote: bool | None = None, min_score: float | None = None, profile: str = "best",
    liked: bool | None = None, hidden: bool | None = None, sent: bool | None = None,
    reached_out: bool | None = None, has_connection: bool | None = None,
    department: str | None = None, industry: str | None = None, language: str | None = None,
    max_years: int | None = None, posted_after: str | None = None,
    referral: bool | None = None, has_description: bool | None = None,
    sort: str = "score", page: int = 1, size: int = 50,
) -> dict:
    """One page of matching jobs plus the full total. List rows carry no
    description, and size is capped: an unbounded page would let one request
    pull the whole dataset, which is what this API exists to avoid."""
    return search.search_jobs(
        db.shared(), q=q, scope=scope, exclude=exclude, company_id=company, city=city,
        status=status, is_remote=remote, min_score=min_score, profile=profile,
        liked=liked, hidden=hidden, sent=sent, reached_out=reached_out,
        has_connection=has_connection, department=department, industry=industry,
        language=language, max_years=max_years, posted_after=posted_after,
        is_referral=referral, has_description=has_description,
        sort=sort, page=page, size=min(max(1, size), 500),
    )


@app.get("/api/jobs/{job_id}", response_class=ORJSONResponse)
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


@app.post("/api/state/import")
def api_import_browser_state(payload: dict) -> dict:
    """Adopt liked/hidden/sent/reached flags out of a browser's localStorage.

    The old static page kept them there, where they could not be queried,
    backed up or seen from another device. The front end offers this once, on
    the origin that page was served from - flags from a page opened off disk
    are in a different origin and need the import script instead."""
    from jobfit.scripts.import_browser_state import import_state

    return import_state(db.shared(), payload)


@app.get("/api/facets", response_class=ORJSONResponse)
def api_facets(
    q: str | None = None, scope: str = "all", exclude: str | None = None,
    company: str | None = None, city: str | None = None, status: str | None = None,
    remote: bool | None = None, min_score: float | None = None, profile: str = "best",
    liked: bool | None = None, hidden: bool | None = None, sent: bool | None = None,
    reached_out: bool | None = None, has_connection: bool | None = None,
    department: str | None = None, industry: str | None = None, language: str | None = None,
    max_years: int | None = None, posted_after: str | None = None,
    referral: bool | None = None, has_description: bool | None = None,
) -> dict:
    """Counts per company, city and status for the current filter - built from
    the same WHERE clause as /api/jobs, so they cannot disagree."""
    return facets.counts(
        db.shared(), q=q, scope=scope, exclude=exclude, company_id=company, city=city,
        status=status, is_remote=remote, min_score=min_score, profile=profile,
        liked=liked, hidden=hidden, sent=sent, reached_out=reached_out,
        has_connection=has_connection, department=department, industry=industry,
        language=language, max_years=max_years, posted_after=posted_after,
        is_referral=referral, has_description=has_description,
    )


@app.get("/api/profiles/scored")
def api_scored_profiles() -> list[str]:
    """The profile ids that actually have scores, for the "score against"
    selector. Reads the scores rather than the CV registry: a CV uploaded
    but never used in a recompute cannot rank anything yet."""
    return facets.scored_profiles(db.shared())


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
