"""
Step-by-step progress of a tenant's creation, for the web UI.

The provisioner calls record() at each stage; build_progress() turns the
resulting events into an ordered list of steps with a state, timings and the
latest message, plus an overall percentage. This is purely informational: a
failure to record or read events never affects provisioning.

A step's state is derived from its LAST event:
    start / info -> running      done -> done      warn -> done (with a warning)
    skip -> skipped              (no events)  -> pending
and, when the tenant is FAILED, the step that was running becomes failed.
"""
import logging
from datetime import timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_event import TenantEvent

logger = logging.getLogger("tenant-operator.progress")

# (key, label) in the order creation runs them.
CREATE_STEPS: list[tuple[str, str]] = [
    ("validate", "Validate request"),
    ("placement", "Select cluster"),
    ("preflight", "Check for existing resources"),
    ("register", "Register tenant"),
    ("secrets", "Write secrets to Vault"),
    ("manifest", "Commit manifest to GitOps"),
    ("database", "Create and seed databases"),
    ("deploy", "Deploy services and wait for pods"),
    ("erep", "Create default eRep"),
    ("ready", "Ready"),
]
_STEP_KEYS = [k for k, _ in CREATE_STEPS]
_RUNNING = {"start", "info"}
_MAX_EVENTS = 300


def record(tenant_id, step: str, state: str, message: str | None = None) -> None:
    """Appends one event. Uses its own short session and never raises."""
    try:
        db = SessionLocal()
        try:
            db.add(TenantEvent(tenant_id=tenant_id, step=step, state=state, message=message))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 -- progress display must never break provisioning
        logger.warning("could not record event %s/%s for tenant %s: %s", step, state, tenant_id, e)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:                      # SQLite returns naive datetimes
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _seconds(a, b) -> float | None:
    if a is None or b is None:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return round(max(0.0, (b - a).total_seconds()), 1)


def build_progress(db: Session, tenant: Tenant) -> dict:
    events = (
        db.query(TenantEvent).filter(TenantEvent.tenant_id == tenant.id)
        .order_by(TenantEvent.id.asc()).limit(_MAX_EVENTS).all()
    )
    by_step: dict[str, list[TenantEvent]] = {}
    for ev in events:
        by_step.setdefault(ev.step, []).append(ev)

    failed = tenant.status == TenantStatus.FAILED
    steps = []
    for key, label in CREATE_STEPS:
        evs = by_step.get(key, [])
        last = evs[-1] if evs else None
        if last is None:
            state = "pending"
        elif last.state in _RUNNING:
            state = "running"
        elif last.state == "done":
            state = "done"
        elif last.state == "warn":
            state = "warn"
        else:
            state = "skipped"
        steps.append({
            "key": key, "label": label, "state": state, "message": last.message if last else None,
            "startedAt": evs[0].created_at if evs else None,
            "finishedAt": last.created_at if last and state not in ("running", "pending") else None,
        })

    # A FAILED tenant: the step that was in flight is the one that failed.
    if failed:
        running = [s for s in steps if s["state"] == "running"]
        target = running[-1] if running else next((s for s in steps if s["state"] == "pending"), None)
        if target is not None:
            target["state"] = "failed"
            target["message"] = tenant.error_message or target["message"]
            target["finishedAt"] = target["finishedAt"] or tenant.updated_at

    has_detail = bool(events)
    total = len(steps)
    finished = sum(1 for s in steps if s["state"] in ("done", "warn", "skipped"))
    running_n = sum(1 for s in steps if s["state"] == "running")
    if tenant.status == TenantStatus.RUNNING and has_detail:
        percent = 100
    elif has_detail:
        percent = int((finished + 0.5 * running_n) / total * 100)
    else:
        percent = None   # tenant predates step tracking -- caller falls back to the status heuristic

    current = next((s for s in steps if s["state"] == "running"), None)
    out_steps = []
    for s in steps:
        out_steps.append({
            "key": s["key"], "label": s["label"], "state": s["state"], "message": s["message"],
            "startedAt": _iso(s["startedAt"]), "finishedAt": _iso(s["finishedAt"]),
            "durationSeconds": _seconds(s["startedAt"], s["finishedAt"]),
        })
    return {
        "tenantId": str(tenant.id),
        "status": tenant.status.value,
        "errorMessage": tenant.error_message,
        "hasDetail": has_detail,
        "percent": percent,
        "currentStep": {"key": current["key"], "label": current["label"]} if current else None,
        "steps": out_steps,
        "events": [
            {"step": e.step, "state": e.state, "message": e.message, "at": _iso(e.created_at)} for e in events
        ],
    }
