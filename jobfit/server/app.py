"""FastAPI app for the jobfit control panel — localhost only, no auth."""

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from jobfit import config, cv
from jobfit.scripts import update_jobs
from jobfit.server import dashboard

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="jobfit control panel")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "panel.html")


@app.get("/api/dashboard")
def api_dashboard() -> dict:
    return dashboard.get_dashboard_stats()


@app.get("/api/profiles")
def api_list_profiles() -> list[dict]:
    return [{"id": pid, **entry} for pid, entry in cv.load_registry().items()]


@app.post("/api/profiles")
async def api_add_profile(name: str = Form(...), file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "CV must be a .docx file")
    config.CV_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = config.CV_PROFILES_DIR / f"_upload_{file.filename}"
    tmp_path.write_bytes(await file.read())
    try:
        profile_id = cv.register_profile(name, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    update_jobs.recompute_stage()
    return {"id": profile_id, **dashboard.get_dashboard_stats()}


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: str) -> dict:
    cv.remove_profile(profile_id)
    update_jobs.recompute_stage()
    return dashboard.get_dashboard_stats()


@app.post("/api/connections")
async def api_upload_connections(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Connections export must be a .csv file")
    config.CONNECTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    config.CONNECTIONS_CSV.write_bytes(await file.read())
    update_jobs.recompute_stage()
    return dashboard.get_dashboard_stats()
