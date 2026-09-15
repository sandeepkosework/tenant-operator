"""
In-process pub/sub so SSE clients (see GET /api/v1/tenant/{id}/events) get
tenant status transitions pushed to them as they happen, instead of having
to poll GET /api/v1/tenant/{id} in a loop.

Single-replica only, same caveat as git_service.py's in-process lock: an
in-memory queue per subscriber doesn't survive a pod restart and isn't
shared across replicas. Swap for Redis pub/sub (or a MongoDB change stream
against the `tenants` collection) if/when the operator needs >1 replica --
callers (provisioner.py, the SSE endpoint) don't need to change, just this
module's internals.
"""
import queue
import threading

from app.models.tenant import STATUS_PROGRESS, TERMINAL_STATUSES, Tenant

_lock = threading.Lock()
_subscribers: dict[str, list["queue.Queue[dict]"]] = {}


def build_event(tenant: Tenant) -> dict:
    return {
        "tenantId": str(tenant.id),
        "tenantName": tenant.tenant_name,
        "status": tenant.status.value,
        "progress": STATUS_PROGRESS.get(tenant.status, 0),
        "errorMessage": tenant.error_message,
    }


def is_terminal_event(event: dict) -> bool:
    return event["status"] in {s.value for s in TERMINAL_STATUSES}


def subscribe(tenant_id: str) -> "queue.Queue[dict]":
    q: "queue.Queue[dict]" = queue.Queue()
    with _lock:
        _subscribers.setdefault(tenant_id, []).append(q)
    return q


def unsubscribe(tenant_id: str, q: "queue.Queue[dict]") -> None:
    with _lock:
        subs = _subscribers.get(tenant_id)
        if not subs:
            return
        if q in subs:
            subs.remove(q)
        if not subs:
            _subscribers.pop(tenant_id, None)


def publish(tenant: Tenant) -> None:
    """Call whenever a tenant's status changes (see provisioner._set_status)."""
    event = build_event(tenant)
    with _lock:
        subs = list(_subscribers.get(str(tenant.id), []))
    for q in subs:
        q.put(event)
