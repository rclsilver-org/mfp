"""FastAPI application entrypoint."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .models import init_db

FRONTEND_DIR = os.environ.get("SCANAPP_FRONTEND_DIR", "/app/frontend")


@asynccontextmanager
async def lifespan(_: FastAPI):
    os.makedirs(settings.scratch_dir, exist_ok=True)
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="Scan", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)
app.include_router(router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/config.js")
async def config_js():
    """Runtime SPA config from env, so the same image works dev/prod.
    Keycloak base URL + realm are derived from the OIDC issuer; the API is same-origin."""
    issuer = settings.oidc_issuer
    if "/realms/" in issuer:
        kc_url, realm = issuer.rsplit("/realms/", 1)
        realm = realm.strip("/")
    else:
        kc_url, realm = issuer, ""
    js = ("window.__SCAN_CONFIG__ = {"
          f"keycloakUrl: {kc_url!r}, realm: {realm!r}, "
          f"clientId: {settings.oidc_client_id!r}, apiBase: '/api'}};")
    return Response(js, media_type="application/javascript")


# serve the built SPA if present (prod); dev uses vite on :5173
if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        index = os.path.join(FRONTEND_DIR, "index.html")
        candidate = os.path.join(FRONTEND_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index)
