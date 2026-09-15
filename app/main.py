import logging

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import cluster, health, tenant, vault
from app.config import get_settings
from app.database import ensure_indexes
from app.socketio_app import sio

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("tenant-operator")

app = FastAPI(
    title="Tenant Operator",
    description="Single entry point for provisioning tenants across the hub-and-spoke cluster fleet.",
    version="1.0.0",
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
app.include_router(tenant.router, prefix=settings.api_prefix)
app.include_router(cluster.router, prefix=settings.api_prefix)
app.include_router(vault.router, prefix=settings.api_prefix)

# Tenant status push -- see app/socketio_app.py. Mounted at /socket.io,
# same host/port as the REST API, no separate process.
app.mount("/socket.io", socketio.ASGIApp(sio, socketio_path=""))


@app.on_event("startup")
def on_startup():
    # Mongo creates collections implicitly on first write -- nothing to
    # migrate, just indexes to declare (see app/database.py).
    ensure_indexes()
    logger.info("tenant-operator started; mongo indexes ensured")
