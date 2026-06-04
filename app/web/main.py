"""FastAPI app factory for the Konggu local Web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.web.routes import export, files, process, results


WEB_ROOT = Path(__file__).resolve().parent

app = FastAPI(
    title="Konggu Local Web",
    description="Local-only Web UI for parsing Konggu schedule PDFs.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=WEB_ROOT / "templates")

app.include_router(files.router)
app.include_router(process.router)
app.include_router(results.router)
app.include_router(export.router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the single-page Konggu workbench."""

    return templates.TemplateResponse(request, "index.html", {})
