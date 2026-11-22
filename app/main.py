"""
app/main.py – FastAPI application factory.

Creates the app, registers routes, and serves the web/ directory.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

_WEB = Path(__file__).parent.parent / "web"

app = FastAPI(title="Namma Route Buddy")

# Mount static assets (CSS, JS)
app.mount("/css", StaticFiles(directory=_WEB / "css"), name="css")
app.mount("/js",  StaticFiles(directory=_WEB / "js"),  name="js")

# Include all API routes
app.include_router(router)


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse((_WEB / "index.html").read_text())
