"""
Triggers the bridge-meta-builder Job once a tenant's database is up and the
workload is RUNNING -- adapted from HBSS's legacy 02_sql_mongo.sh +
meta-builder/ Node.js scripts (see the meta-builder/ directory at this
repo's root for the actual reference implementation this was ported from,
kept there verbatim alongside the adapted copy this Job's image is built
from). Separate from meta_builder_job.py, which only creates the empty
database + login -- this Job does everything after that:
  - runs the real SQL schema (QRaie-Table-Script) and default-data inserts
    (QRaie-Database-Default-Inserts) against the tenant's database
  - seeds this tenant's own MongoDB: a bridgeMetaInfo aggregate document,
    an admin user + auth/RBAC record, default WFM lookup data (activity
    types, paycodes, departments, sample employees), default ControlOps
    actors, and default IoT lookups/devices

Same reuse-not-simulate pattern as meta_builder_job.py: submits a real
batch/v1 Job on the hub, in tenant-operator's own namespace, via
in-cluster config. Gated on the same "does this tenant have a
controlops-server secret in Vault" check meta_builder_job.py uses -- if
there's nothing to migrate a real DB for, there's nothing for this Job to
seed either.

Unlike the legacy script, this Job never talks to Vault itself (no `vault`
binary/token baked into its image) -- tenant-operator already has
everything it needs from the same Vault reads meta_builder_job.py already
does, and hands it over as plain env vars.
"""
import logging
import threading
import time
from urllib.parse import urlparse

from kubernetes import client, config as k8s_config
from kubernetes.client import ApiException

from app.config import get_settings
from app.models.tenant import Tenant
from app.services import vault_service

logger = logging.getLogger("tenant-operator.bridge_meta_builder_job")
settings = get_settings()

# Same gating service as meta_builder_job.py -- see that module's
# _DB_SERVICE comment for why this is the one service whose presence
# means there's a real database to seed at all.
_DB_SERVICE = "controlops-server"
# qraie-api-gateway's own generated JWT signing keys (QRAIE_BRIDGE_GENERATED_KEYS
# in vault_service.py) -- reused here rather than generating a second,
# separate JWT secret, since these are meant to sign/validate tokens for
# the same workplace/bridge service either way.
_JWT_SERVICE = "qraie-api-gateway"

_JOB_POLL_INTERVAL_SECONDS = 5
# This Job does substantially more work than meta_builder_job.py's (schema
# + inserts + a full Mongo seed across ~8 collections), so it gets a
# longer timeout budget.
_JOB_TIMEOUT_SECONDS = 300

_SQLCMD_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"


class BridgeMetaBuilderJobError(Exception):
    pass


def _get_hub_api_client() -> client.ApiClient:
    """Same reasoning as meta_builder_job.py's identical helper -- tenant-
    operator runs on the hub itself, so this is always in-cluster config."""
    k8s_config.load_incluster_config()
    return client.ApiClient()


def _parse_mongo_uri(uri: str) -> tuple[str, str, str, str]:
    """Splits settings.mongo_env_config_uri into (host, port, username,
    password) -- the individual fields tenantBridgeMeta.js's
    bridgeMetaBuilderService() needs for the values it bakes into
    ctrlOpsObj/wfmObj's own connection strings. Returns empty strings for
    any piece missing rather than raising -- build_bridge_meta_builder_job()
    already treats a fully-empty mongo config as "nothing to seed" via its
    own settings check."""
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    port = str(parsed.port) if parsed.port else "27017"
    username = parsed.username or ""
    password = parsed.password or ""
    return host, port, username, password


def _mongo_base_uri_for_script(uri: str) -> str:
    """tenantBridgeMeta.js's getTenantConn() builds its own tenant-specific
    connection string as `${QRAIEAI_MONGODB_URI}${dbName}?authSource=admin`
    -- so this needs to be the bare scheme+auth+host+port with a trailing
    slash and nothing else (no db name, no query string) for that string
    concatenation to produce a valid URI."""
    base = uri.partition("?")[0]
    return base if base.endswith("/") else f"{base}/"


def build_bridge_meta_builder_job(tenant: Tenant, admin_password: str) -> dict | None:
    """Returns None if there's nothing to seed for this tenant -- same
    gating meta_builder_job.py uses (no controlops-server secret means no
    service in this tenant actually models a real DB connection)."""
    db_secret = vault_service.read_qraie_bridge_tenant_secret(tenant.slug, _DB_SERVICE)
    if not db_secret or not db_secret.get("DB_NAME"):
        return None

    if not settings.mongo_env_config_uri:
        logger.info(
            "[bridge-meta] tenant=%s skipping: mongo_env_config_uri not configured",
            tenant.tenant_name,
        )
        return None

    jwt_secret = vault_service.read_qraie_bridge_tenant_secret(tenant.slug, _JWT_SERVICE) or {}

    mongo_host, mongo_port, mongo_user, mongo_pass = _parse_mongo_uri(settings.mongo_env_config_uri)
    mongo_base_uri = _mongo_base_uri_for_script(settings.mongo_env_config_uri)

    job_name = f"bridge-meta-{tenant.slug}-{int(time.time())}"[:63].rstrip("-")

    # tenantBridgeMeta.js's positional CLI args, in the legacy script's own
    # order (see main()'s `process.argv[2..7]`) -- kept identical to the
    # legacy 02_sql_mongo.sh -> node invocation so the reference
    # implementation in meta-builder/ and this Job stay directly
    # comparable. TENANT_ID (arg 1) is tenant.tenant_name (the bare,
    # human-chosen name, e.g. "hbss"), NOT tenant.slug ("hbss-15") -- the
    # legacy system's own Mongo DB name and public hostname were both
    # built from the bare name, never a sequence number (confirmed against
    # a real live example: "hbss-bridgestg.qraie.ai" only decomposes
    # correctly as tenant_name="hbss" + "-bridge" + "stg" + ".qraie.ai").
    # TENANT_NAME (last arg) is tenant.display_name -- the legacy script
    # uses this one (not its own separate DISPLAY_NAME arg, which is
    # validated but never actually used in its business logic) for every
    # human-readable name it seeds (Mongo users.fullName, the SQL
    # TENANT.tenant_name/description columns).
    args = [
        tenant.tenant_name,
        settings.bridge_base_domain,
        tenant.email or "",
        tenant.display_name or tenant.tenant_name,
        admin_password,
        tenant.display_name or tenant.tenant_name,
    ]

    env = [
        {"name": "DB_HOST", "value": db_secret.get("DB_HOST", "")},
        {"name": "DB_PORT", "value": str(settings.mssql_admin_port)},
        {"name": "DB_USER", "value": db_secret.get("DB_USER", "")},
        {"name": "DB_PASSWORD", "value": db_secret.get("DB_PASSWORD", "")},
        {"name": "REDIS_HOST", "value": db_secret.get("REDIS_HOST", "")},
        {"name": "REDIS_PASSWORD", "value": db_secret.get("REDIS_PASSWORD", "")},
        {"name": "MONGO_DB_HOST", "value": mongo_host},
        {"name": "MONGO_DB_PORT", "value": mongo_port},
        {"name": "MONGO_DB_USERNAME", "value": mongo_user},
        {"name": "MONGO_DB_PASSWORD", "value": mongo_pass},
        {"name": "QRAIEAI_MONGODB_URI", "value": mongo_base_uri},
        {"name": "JWT_SECRETKEY", "value": jwt_secret.get("G_JWT_SECRETKEY", "")},
        {"name": "RT_SECRETKEY", "value": jwt_secret.get("G_RT_SECRETKEY", "")},
        {"name": "IS_STAGING", "value": "true" if settings.environment == "stage" else "false"},
    ]

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": settings.bridge_meta_builder_job_namespace,
            "labels": {
                "app.kubernetes.io/component": "bridge-meta-builder",
                "tenant": tenant.tenant_name,
            },
        },
        "spec": {
            "backoffLimit": 2,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "bridge-meta-builder",
                            "image": f"{settings.bridge_meta_builder_image_repository}:{settings.bridge_meta_builder_image_tag}",
                            "args": args,
                            "env": env,
                        }
                    ],
                }
            },
        },
    }


def _watch_job(api_client: client.ApiClient, job_name: str, namespace: str) -> None:
    """Same polling pattern as meta_builder_job.py's identical helper --
    runs on a background thread, must never raise past
    trigger_bridge_meta_builder_job()'s fire-and-forget call site."""
    batch_v1 = client.BatchV1Api(api_client)
    deadline = time.monotonic() + _JOB_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        try:
            job = batch_v1.read_namespaced_job_status(job_name, namespace)
        except ApiException as e:
            logger.warning("[bridge-meta] job=%s status check failed (will retry): %s", job_name, e)
            time.sleep(_JOB_POLL_INTERVAL_SECONDS)
            continue

        status = job.status
        if status.succeeded:
            logger.info("[bridge-meta] job=%s completed successfully", job_name)
            return
        if status.failed:
            logger.error(
                "[bridge-meta] job=%s failed -- check the Job's own pod logs "
                "(kubectl logs -n %s job/%s)",
                job_name, namespace, job_name,
            )
            return
        time.sleep(_JOB_POLL_INTERVAL_SECONDS)

    logger.error("[bridge-meta] job=%s did not complete within %ds", job_name, _JOB_TIMEOUT_SECONDS)


def trigger_bridge_meta_builder_job(tenant: Tenant, admin_password: str) -> str | None:
    """Submits the real schema+inserts+Mongo-seed Job for this tenant, if
    there's a controlops-server secret to seed against. Returns the Job
    name, or None if there's nothing to seed -- logged, not raised, same
    as meta_builder_job.py's identical soft-skip behavior."""
    job = build_bridge_meta_builder_job(tenant, admin_password)
    if job is None:
        logger.info(
            "[bridge-meta] tenant=%s skipping: no '%s' secret in Vault (service not enabled for this tenant)",
            tenant.tenant_name, _DB_SERVICE,
        )
        return None

    job_name = job["metadata"]["name"]
    namespace = settings.bridge_meta_builder_job_namespace

    logger.info(
        "[bridge-meta] tenant=%s submitting schema+inserts+mongo-seed Job '%s' in namespace '%s' on the hub",
        tenant.tenant_name, job_name, namespace,
    )

    api_client = _get_hub_api_client()
    batch_v1 = client.BatchV1Api(api_client)
    try:
        batch_v1.create_namespaced_job(namespace, job)
    except ApiException as e:
        logger.error("[bridge-meta] tenant=%s failed to submit Job: %s", tenant.tenant_name, e)
        return None

    threading.Thread(target=_watch_job, args=(api_client, job_name, namespace), daemon=True).start()

    return job_name
