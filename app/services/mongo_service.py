"""
Mirrors each qraie-bridge tenant's resolved env config (the same data
vault_service.write_initial_qraie_bridge_tenant_secrets() writes to Vault)
into that tenant's own MongoDB database, so it can be browsed/queried
without a Vault client -- one database per tenant (named after the tenant's
slug), one collection per service group, one document per service holding
all of that service's env keys.

Deliberately NOT the source of truth for anything -- Vault (via the chart's
envFrom, see helm-chart-bridge's README) is what pods actually read at
runtime. This is a read-only-for-humans mirror, written once at tenant
creation alongside the Vault write, never read back by this codebase.

Only writes for real when settings.mongo_env_config_enabled is True;
otherwise logs/echoes what would happen, same pattern as vault_service.py
and crossplane_service.py.
"""
import logging

from app.config import get_settings

logger = logging.getLogger("tenant-operator.mongo_service")
settings = get_settings()

_client = None


class MongoServiceError(Exception):
    pass


def _get_client():
    global _client
    if _client is not None:
        return _client

    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    if not settings.mongo_env_config_uri:
        raise MongoServiceError("mongo_env_config_uri is not configured")
    try:
        client = MongoClient(settings.mongo_env_config_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except PyMongoError as e:
        raise MongoServiceError(f"failed to connect to MongoDB: {e}") from e

    _client = client
    return _client


def _redact(data: dict) -> dict:
    return {k: ("***" if "password" in k.lower() or "secret" in k.lower() or "token" in k.lower() else v) for k, v in data.items()}


# service name -> which collection (service group) its document lands in.
# "common" is vault_service's own tenant-wide common-config entry (see
# QRAIE_BRIDGE_SERVICE_KEYS["common"]), not one of the ~32 chart services --
# given its own collection rather than folded into any one app group.
# Every other grouping mirrors the chart's own ingressPath prefixes where a
# service has one (e.g. /workplace/, /controlops/, /galaxy/), or groups by
# service-name family for internal services with no ingress path of their
# own (redis sidecars, an app's paired *-microservice/*-server).
QRAIE_BRIDGE_SERVICE_GROUPS: dict[str, str] = {
    "common": "common",

    "bridge": "bridge",
    "qraie-redis-shared": "bridge",
    "qraie-redis": "bridge",

    "controlops-server": "controlops",
    "bridge-cp-conductor": "controlops",
    "bridge-cp-conductor-redis": "controlops",

    "erep-server": "galaxy",
    "mcp-client": "galaxy",
    "mcp-server": "galaxy",

    "qraie-api-gateway": "workplace",
    "qraie-ui": "workplace",
    "admin-panel": "workplace",
    "microservice-qraie": "workplace",

    "wfm-api-gateway": "wfm",
    "wfm-ui": "wfm",
    "wfm-microservice": "wfm",

    "tranops-ui": "tranops",
    "tranops-backend": "tranops",

    "iot-broker-data": "iot-broker",
    "iot-broker-config": "iot-broker",
    "iot-broker-broker": "iot-broker",
    "iot-broker-admin": "iot-broker",
    "iot-broker-web": "iot-broker",
    "iot-broker-loconav-vision": "iot-broker",

    "enrollment-api": "enrollment",
    "acl-server": "acl",

    "prism-ui": "prism",
    "prism-backend": "prism",
    "prism-scanner": "prism",

    "voxflow": "voxflow",
    "scheduler-agent": "scheduler-agent",
    "radicale": "radicale",
}


def write_tenant_env_config(tenant_slug: str, service_data: dict[str, dict]) -> None:
    """Call once, right alongside write_initial_qraie_bridge_tenant_secrets()
    -- service_data is exactly that function's return value (service name ->
    its resolved env dict), so this never re-derives or re-classifies
    anything, just mirrors what was already written to Vault.

    Database = tenant_slug. Collection = that service's group
    (QRAIE_BRIDGE_SERVICE_GROUPS). Document _id = service name, so a
    service's doc is replaced (not duplicated) on a later tenant update
    that calls this again."""
    logger.info(
        "[mongo] tenant=%s writing env config for %d services%s",
        tenant_slug, len(service_data),
        " (echoed, mongo_env_config_enabled=false)" if not settings.mongo_env_config_enabled else "",
    )

    if not settings.mongo_env_config_enabled:
        for service, data in service_data.items():
            group = QRAIE_BRIDGE_SERVICE_GROUPS.get(service, service)
            logger.info("[mongo] tenant=%s would write %s.%s: %s", tenant_slug, group, service, _redact(data))
        return

    client = _get_client()
    db = client[tenant_slug]
    for service, data in service_data.items():
        group = QRAIE_BRIDGE_SERVICE_GROUPS.get(service, service)
        doc = {"_id": service, **data}
        db[group].replace_one({"_id": service}, doc, upsert=True)
        logger.info("[mongo] tenant=%s wrote %s.%s: %s", tenant_slug, group, service, _redact(data))

    logger.info("[mongo] tenant=%s env config ready (%d services, db=%s)", tenant_slug, len(service_data), tenant_slug)
