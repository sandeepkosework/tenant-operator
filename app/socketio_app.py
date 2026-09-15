"""
Socket.IO transport for tenant status push (replaces the earlier SSE
endpoint). Mounted at /socket.io by app/main.py, alongside the plain REST
API -- same server/port, no separate process.

status_bus.py (the in-process pub/sub) is transport-agnostic; this module
is just one consumer of it. A client:

    const socket = io("http://localhost:8000");
    socket.emit("subscribe", { tenantId: "<uuid>" });
    socket.on("status", ({ status, progress, errorMessage }) => { ... });
    socket.on("error", ({ message }) => { ... });

receives one "status" event immediately (current state) plus one per
subsequent transition, until a terminal status ends that subscription.
Disconnecting (or the tenant reaching a terminal status) cleans up the
underlying status_bus subscription -- nothing leaks per client.
"""
import asyncio
import logging
import queue
import uuid

import socketio

from app.config import get_settings
from app.database import SessionLocal
from app.models.tenant import Tenant
from app.services import status_bus

logger = logging.getLogger("tenant-operator.socketio")
settings = get_settings()

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=settings.cors_allow_origins)

# sid -> relay task, so disconnect can cancel it and unsubscribe from status_bus cleanly.
_relay_tasks: dict[str, asyncio.Task] = {}


async def _relay_status(sid: str, tenant_id: str, q: "queue.Queue[dict]") -> None:
    try:
        while True:
            # queue.Queue.get() blocks a thread, not the event loop -- run it
            # off-thread so other clients/requests keep being served.
            event = await asyncio.to_thread(q.get)
            await sio.emit("status", event, to=sid)
            if status_bus.is_terminal_event(event):
                break
    finally:
        status_bus.unsubscribe(tenant_id, q)
        _relay_tasks.pop(sid, None)


@sio.event
async def connect(sid, environ):
    logger.info("socket.io client connected sid=%s", sid)


@sio.event
async def disconnect(sid):
    task = _relay_tasks.pop(sid, None)
    if task:
        task.cancel()
    logger.info("socket.io client disconnected sid=%s", sid)


@sio.event
async def subscribe(sid, data):
    """Client -> server: {"tenantId": "<uuid>"}. Server -> client: "status" events."""
    raw_id = (data or {}).get("tenantId")
    if not raw_id:
        await sio.emit("error", {"message": "subscribe requires {tenantId: <uuid>}"}, to=sid)
        return

    try:
        tenant_id = uuid.UUID(str(raw_id))
    except ValueError:
        await sio.emit("error", {"message": f"'{raw_id}' is not a valid tenant id"}, to=sid)
        return

    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
    finally:
        db.close()

    if tenant is None:
        await sio.emit("error", {"message": f"tenant '{raw_id}' not found"}, to=sid)
        return

    initial_event = status_bus.build_event(tenant)
    await sio.emit("status", initial_event, to=sid)

    if status_bus.is_terminal_event(initial_event):
        return

    q = status_bus.subscribe(str(tenant_id))
    _relay_tasks[sid] = asyncio.create_task(_relay_status(sid, str(tenant_id), q))
