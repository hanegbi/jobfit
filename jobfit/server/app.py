"""FastAPI app for the jobfit control panel — localhost only, no auth."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from jobfit.server import dashboard

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="jobfit control panel")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "panel.html")


@app.get("/api/dashboard")
def api_dashboard() -> dict:
    return dashboard.get_dashboard_stats()
