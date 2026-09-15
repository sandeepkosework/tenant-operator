"""
Read-only Argo CD integration. The operator never creates or patches
Application resources directly -- the ApplicationSet git generator does
that automatically once tenants/{name}.yaml lands in the repo (see
templates/applicationset.example.yaml). This module only *observes*
status so the operator can report accurate tenant state back to callers.

Used by provisioner.py's inline wait loop during a tenant's own rollout
(onboarding or update) -- tenants are not monitored after that. It cares
about the same distinction throughout: Argo CD being *unreachable*
(transient -- keep retrying) versus an Application being definitively
*failed* (real -- stop and report). See classify().
"""
import httpx

from app.config import get_settings

settings = get_settings()


class ArgoCDServiceError(Exception):
    pass


class ArgoCDUnreachableError(ArgoCDServiceError):
    """Argo CD itself didn't respond (network error, 5xx, timeout) --
    distinct from a 200 response reporting a real application failure.
    Callers should treat this as transient and retry, not fail outright."""


_DEFINITIVE_FAILURE_OPERATION_PHASES = {"Failed", "Error"}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.argocd_server,
        headers={"Authorization": f"Bearer {settings.argocd_token}"} if settings.argocd_token else {},
        verify=settings.argocd_verify_tls,
        timeout=10.0,
    )


def get_application_status(application_name: str) -> dict:
    """
    Returns a dict like:
      {"exists": True, "health": "Healthy", "sync": "Synced",
       "operationPhase": "Succeeded", "message": None, "resources": [...]}
    or {"exists": False} if the ApplicationSet hasn't materialized the
    Application yet (normal right after a fresh git push).

    Raises ArgoCDUnreachableError if Argo CD itself didn't respond --
    callers should retry, not treat this as the application having failed.
    """
    try:
        with _client() as client:
            resp = client.get(f"/api/v1/applications/{application_name}")
    except httpx.HTTPError as e:
        raise ArgoCDUnreachableError(f"could not reach Argo CD: {e}") from e

    if resp.status_code == 404:
        return {"exists": False}
    if resp.status_code >= 500:
        raise ArgoCDUnreachableError(
            f"Argo CD returned {resp.status_code} (server error) for application '{application_name}'"
        )
    if resp.status_code != 200:
        raise ArgoCDServiceError(
            f"Argo CD returned {resp.status_code} for application '{application_name}': {resp.text}"
        )

    data = resp.json()
    app_status = data.get("status", {})
    health = app_status.get("health", {}) or {}
    sync = app_status.get("sync", {}) or {}
    operation_state = app_status.get("operationState", {}) or {}
    conditions = app_status.get("conditions", []) or []

    message = health.get("message") or operation_state.get("message")
    if not message and conditions:
        message = conditions[0].get("message")

    return {
        "exists": True,
        "health": health.get("status", "Unknown"),
        "sync": sync.get("status", "Unknown"),
        "operationPhase": operation_state.get("phase"),
        "message": message,
        "resources": app_status.get("resources", []),
    }


def classify(app_status: dict) -> str:
    """
    One of:
      "missing"     -- Application doesn't exist yet (normal right after a fresh push)
      "healthy"     -- Healthy + Synced
      "failed"      -- the last sync operation itself Failed/Error (a real,
                       reportable failure -- don't wait out the full timeout for this)
      "progressing" -- anything else, INCLUDING Degraded health -- Argo CD
                       commonly reports a resource as transiently Degraded
                       while a sync that already succeeded is still settling
                       (e.g. a Deployment/ExternalSecret not yet caught up),
                       which is not the same as a real failure. Only a
                       failed/errored *operation* is treated as definitive;
                       Degraded health alone just keeps the poll loop going
                       until it resolves to Healthy or the timeout expires.
    """
    if not app_status.get("exists"):
        return "missing"
    if app_status.get("health") == "Healthy" and app_status.get("sync") == "Synced":
        return "healthy"
    if app_status.get("operationPhase") in _DEFINITIVE_FAILURE_OPERATION_PHASES:
        return "failed"
    return "progressing"


def is_healthy_and_synced(application_name: str) -> bool:
    """Kept for simple callers that only care about the yes/no outcome."""
    return classify(get_application_status(application_name)) == "healthy"
