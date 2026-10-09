"""
Creates the tenant's default eRep ("Noah") through the tenant's own erep-server
API once the tenant is RUNNING -- the equivalent of infra-runner's
scripts/07_setup_erep.sh, which POSTed the same payload to
https://<tenant host>/galaxy/erepapi/api/ereps after the services were up.

Off by default (erep_setup_enabled=false). The call goes to the tenant's PUBLIC
URL, so the operator must be able to reach it (DNS, TLS and egress from wherever
the operator runs): the operator deliberately never talks to a spoke cluster's
API to write anything, and the spokes are not reachable by in-cluster DNS from
the hub. If that URL is not reachable from the operator, point
erep_setup_url_template at an address that is.

Runs once, at the end of provisioning -- it is NOT idempotent on the eRep side
(infra-runner's wasn't either), so it is never retried after a success and is
not called from the update flow.
"""
import json
import logging
import time
from pathlib import Path

import httpx

from app.config import get_settings
from app.models.tenant import Tenant

logger = logging.getLogger("tenant-operator.erep_setup")
settings = get_settings()

_PAYLOAD_TEMPLATE = Path(__file__).resolve().parent.parent.parent / "templates" / "erep_default_payload.json"


class ErepSetupError(Exception):
    pass


def build_payload(tenant: Tenant) -> dict:
    """The default eRep payload, with a per-tenant email so it is unique."""
    email = f"erep-noah-{tenant.tenant_name}@example.com"
    text = _PAYLOAD_TEMPLATE.read_text(encoding="utf-8").replace("{{EREP_EMAIL}}", email)
    return json.loads(text)


def erep_url(tenant: Tenant) -> str:
    return settings.erep_setup_url_template.format(domain=tenant.domain, tenant=tenant.tenant_name, slug=tenant.slug)


def setup_default_erep(tenant: Tenant) -> None:
    """POSTs the default eRep. Does nothing if disabled. Retries (the pods can
    be Ready before the ingress/DNS/TLS path is live) and raises ErepSetupError
    once the attempts are used up."""
    if not settings.erep_setup_enabled:
        logger.info("[erep] tenant=%s skipped: erep_setup_enabled=false", tenant.tenant_name)
        return
    if not tenant.domain and "{domain}" in settings.erep_setup_url_template:
        raise ErepSetupError("tenant has no domain, cannot build the erep-server URL")

    url = erep_url(tenant)
    payload = build_payload(tenant)
    attempts = max(1, settings.erep_setup_attempts)
    last = "no attempt made"

    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.post(
                url, json=payload,
                timeout=settings.erep_setup_timeout_seconds,
                verify=settings.erep_setup_verify_tls,
            )
            if resp.status_code in (200, 201):
                logger.info("[erep] tenant=%s default eRep created (HTTP %s) via %s", tenant.tenant_name, resp.status_code, url)
                return
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
        logger.warning("[erep] tenant=%s attempt %d/%d failed (%s)", tenant.tenant_name, attempt, attempts, last)
        if attempt < attempts:
            time.sleep(settings.erep_setup_retry_delay_seconds)

    raise ErepSetupError(f"default eRep was not created after {attempts} attempts ({url}): {last}")
