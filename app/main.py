import logging

import socketio
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api import auth, cluster, health, tenant, ui, vault
from app.auth import require_admin
from app.config import get_settings
from app.database import Base, engine
from app.socketio_app import sio

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("tenant-operator")

DESCRIPTION = """
Single entry point for provisioning tenants across the hub-and-spoke cluster fleet.
The operator writes a per-tenant `values.yaml` to the GitOps repo and Argo CD does the deploying.

## Authentication
Every endpoint except `/health` and `/api/v1/auth/login` needs **one** of:

* **Bearer token** - `POST /api/v1/auth/login` with the admin username/password, then send
  `Authorization: Bearer <token>`.
* **API key** - send `X-API-Key: <API_KEY>` (best for scripts/curl).

Click **Authorize** (top right) to try endpoints from this page.
`401` = missing/invalid credentials, `503` = the operator has no `ADMIN_PASSWORD`/`API_KEY` configured.

## Tenant status lifecycle
`PENDING` -> `VALIDATING` -> `GIT_COMMITTED` -> `SYNCING` -> **`RUNNING`**
(or **`FAILED`**). Updates use `UPDATING`; deletion goes `DELETING` -> `DELETED`.
`progress` (0-100) in tenant responses maps to these stages and is meant for a progress bar.

## Live status
Besides polling `GET /api/v1/tenant/{id}`, connect Socket.IO at `/socket.io` with
`auth: {token}` (or `{apiKey}`), emit `subscribe` with `{tenantId}` and listen for `status` events.

## Web UI
A tenant dashboard with progress bars and delete is at [`/ui`](/ui).
"""

TAGS = [
    {"name": "auth", "description": "Log in to obtain a bearer token."},
    {"name": "tenant", "description": "Create, inspect, update and delete tenants. Create/update/delete are asynchronous (202) - follow progress via `status`/`progress`."},
    {"name": "cluster", "description": "Read-only view of the registered spoke clusters and the tenants placed on each."},
    {"name": "vault", "description": "Platform-wide default config shared by every qraie-bridge service (applies to tenants created afterwards)."},
    {"name": "health", "description": "Probe endpoint (no auth)."},
]

app = FastAPI(
    title="Tenant Operator",
    description=DESCRIPTION,
    version="1.0.0",
    openapi_tags=TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
)

# So a browser-based frontend can call the REST API directly. Tighten
# CORS_ALLOW_ORIGINS to your real frontend origin(s) once you have one --
# "*" is fine for local dev/POC only. Socket.IO has its own separate
# cors_allowed_origins, set from the same setting in socketio_app.py.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(tenant.router, prefix=settings.api_prefix, dependencies=[Depends(require_admin)])
app.include_router(cluster.router, prefix=settings.api_prefix, dependencies=[Depends(require_admin)])
app.include_router(vault.router, prefix=settings.api_prefix, dependencies=[Depends(require_admin)])
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(ui.router)


@app.get("/doc", include_in_schema=False)
def doc_redirect():
    return RedirectResponse("/docs")

# Tenant status push -- see app/socketio_app.py. Mounted at /socket.io,
# same host/port as the REST API, no separate process.
app.mount("/socket.io", socketio.ASGIApp(sio, socketio_path=""))


@app.on_event("startup")
def on_startup():
    # For a real deployment, replace this with Alembic migrations
    # (see scripts/ and README) -- create_all is fine for first bring-up.
    Base.metadata.create_all(bind=engine)
    logger.info("tenant-operator started; tables ensured")
