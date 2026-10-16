"""
Registers a new tenant with the qraie onboarding server (the "config API").

Several tenant services -- erep-server, wfm-microservice, mcp-client -- ask that
server for their MongoDB connection string at startup (GET
<CONFIG_API_URL>/<tenant id>) and crash-loop when it has no record for the
tenant. The legacy flow never registered tenants itself; the record was added
by hand. This does it as part of provisioning: one POST {tenantId,
connectionString} to the server's `POST /config`.

Opt-in: does nothing (one warning) until ONBOARDING_SERVER_URL is set, so
deployments that don't use the server are unaffected. When it IS set, a failure
raises OnboardingRegistrationError and provision_tenant marks the tenant FAILED
-- without the record the tenant's pods could never become healthy anyway.

Idempotent: the server has no update route and rejects a duplicate tenantId, so
an existing record is treated as already registered (with a warning if it holds
a different connection string, which only a manual fix on the server can change).
Removing the record is part of an explicit data purge only, not of a plain tenant
delete: delete_tenant_record() sends DELETE <url>/config/<tenant name>.
"""
import logging
import time

import httpx

from app.config import get_settings
from app.models.tenant import Tenant
from app.services import vault_service

logger = logging.getLogger("tenant-operator.onboarding")
settings = get_settings()


class OnboardingRegistrationError(Exception):
    pass


def _base_url() -> str:
    return (settings.onboarding_server_url or "").rstrip("/")


def _existing_connection_string(tenant_id: str) -> str | None:
    """The connectionString the server already holds for this tenant, or None."""
    resp = httpx.get(
        f"{_base_url()}/config/{tenant_id}",
        timeout=settings.onboarding_server_timeout_seconds,
        headers=_headers(),
    )
    if resp.status_code != 200:
        return None
    return (resp.json().get("data") or {}).get("connectionString")


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.onboarding_server_token}"} if settings.onboarding_server_token else {}


def register_tenant(tenant: Tenant) -> None:
    """Creates the tenant's record. Does nothing if ONBOARDING_SERVER_URL is unset."""
    if not settings.onboarding_server_url:
        logger.warning("[onboarding] tenant=%s skipped: ONBOARDING_SERVER_URL is not set", tenant.tenant_name)
        return

    # tenant_name, not slug: it is what the tenant's pods send as TENANT_ID and
    # the database is named "<tenant_name>-bridge" (see vault_service).
    tenant_id = tenant.tenant_name
    connection_string = vault_service._tenant_mongodb_uri(tenant_id)
    if not connection_string:
        raise OnboardingRegistrationError("MONGO_ENV_CONFIG_URI is not configured, so there is no connection string to register")

    attempts = max(1, settings.onboarding_server_attempts)
    last = "no attempt made"
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(
                f"{_base_url()}/config",
                json={"tenantId": tenant_id, "connectionString": connection_string},
                timeout=settings.onboarding_server_timeout_seconds,
                headers=_headers(),
            )
            if resp.status_code in (200, 201):
                logger.info("[onboarding] tenant=%s registered as '%s'", tenant.tenant_name, tenant_id)
                return
            # Duplicate tenantId (the server answers 400/409): fine if the record is there.
            existing = _existing_connection_string(tenant_id)
            if existing is not None:
                if existing != connection_string:
                    logger.warning(
                        "[onboarding] tenant=%s already registered with a DIFFERENT connection string; "
                        "the server has no update route, fix it there by hand", tenant.tenant_name,
                    )
                else:
                    logger.info("[onboarding] tenant=%s already registered", tenant.tenant_name)
                return
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
        logger.warning("[onboarding] tenant=%s attempt %d/%d failed (%s)", tenant.tenant_name, attempt, attempts, last)
        if attempt < attempts:
            time.sleep(settings.onboarding_server_retry_delay_seconds)
    raise OnboardingRegistrationError(
        f"could not register with the onboarding server after {attempts} attempts ({_base_url()}): {last}"
    )


def delete_tenant_record(tenant: Tenant, dry_run: bool = False) -> str:
    """Removes the tenant's record from the onboarding server (DELETE /config/<tenant name>),
    used by the data purge. Returns "deleted", "absent" (no such record) or "would_delete"
    (dry run, record exists); "skipped" if ONBOARDING_SERVER_URL is unset. Raises
    OnboardingRegistrationError if the server cannot be reached or refuses."""
    if not settings.onboarding_server_url:
        return "skipped"
    tenant_id = tenant.tenant_name
    try:
        if dry_run:
            return "would_delete" if _existing_connection_string(tenant_id) is not None else "absent"
        resp = httpx.delete(
            f"{_base_url()}/config/{tenant_id}",
            timeout=settings.onboarding_server_timeout_seconds,
            headers=_headers(),
        )
    except httpx.HTTPError as e:
        raise OnboardingRegistrationError(f"onboarding server unreachable: {type(e).__name__}: {e}") from e
    if resp.status_code == 404:
        return "absent"
    if resp.status_code in (200, 204):
        logger.info("[onboarding] tenant=%s record removed", tenant.tenant_name)
        return "deleted"
    raise OnboardingRegistrationError(f"onboarding server answered HTTP {resp.status_code}: {resp.text[:200]}")
