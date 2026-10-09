"""
Triggers the bridge-meta-builder Kubernetes Job once a tenant's database is
up and the workload is RUNNING. That Job connects to the platform's MSSQL
server as an admin login and ensures the tenant's own database + login
actually exist with the same password tenant-operator already generated
and wrote to Vault -- closing the gap where Vault holds a credential
nothing on the DB server recognizes.

No separate image is built for this: the Job reuses the same
mcr.microsoft.com/mssql/server image the platform's own MSSQL instance
runs (it already ships sqlcmd at /opt/mssql-tools18/bin/sqlcmd), running
an inline sqlcmd script as the Job's command -- nothing new to build or
push.

Only wired up for app_type="qraie-bridge" tenants that have a
"controlops-server" secret in Vault (the one qraie-bridge service whose
Vault schema models DB_HOST/DB_USER/DB_PASSWORD/DB_NAME -- see
vault_service.QRAIE_BRIDGE_SERVICE_KEYS). Tenants without that service
enabled, or app_type="workplace" tenants, get a no-op: there's nothing for
this Job to seed.

Runs on the hub cluster (tenant-operator's own cluster), via in-cluster
config -- NOT the spoke the tenant's workload runs on, since that's where
tenant-operator itself, and the MSSQL server it talks to, actually live.

The admin password is passed through as a Job env var, sourced from this
operator's own Settings (never logged, never persisted to the tenant-
operator database) -- same as the tenant's own generated DB_PASSWORD,
which this module reads back from Vault rather than needing its own copy
threaded through provisioner.py.
"""
import logging
import time

from kubernetes import client, config as k8s_config
from kubernetes.client import ApiException

from app.config import get_settings
from app.models.tenant import Tenant
from app.services import vault_service

logger = logging.getLogger("tenant-operator.meta_builder_job")
settings = get_settings()

# The one qraie-bridge service whose Vault schema models a real DB
# connection (DB_HOST/DB_USER/DB_PASSWORD/DB_NAME) -- see
# vault_service.QRAIE_BRIDGE_SERVICE_KEYS. If a tenant doesn't have this
# service's secret in Vault (not enabled, or app_type != "qraie-bridge"),
# there's nothing for this Job to seed.
_DB_SERVICE = "controlops-server"

_JOB_POLL_INTERVAL_SECONDS = 5
_JOB_TIMEOUT_SECONDS = 180

# Reuses the platform's own MSSQL image purely for its bundled sqlcmd --
# this Job never starts a SQL Server instance, just connects out to one.
_SQLCMD_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
_SQLCMD_BIN = "/opt/mssql-tools18/bin/sqlcmd"

# CREATE/ALTER LOGIN and CREATE DATABASE can't parameterize identifiers or
# object names via sqlcmd -- $(...) substitutes sqlcmd scripting variables
# (-v below), not shell variables, so this is safe from shell injection.
# tenant_db_name/tenant_db_user both come from tenant-operator's own
# generated slug/service naming (see Tenant.slug), never raw user input.
_SEED_SQL = r"""
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'$(TENANT_DB_NAME)')
BEGIN
    PRINT 'creating database $(TENANT_DB_NAME)';
    EXEC('CREATE DATABASE [$(TENANT_DB_NAME)]');
END
ELSE
    PRINT 'database $(TENANT_DB_NAME) already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.sql_logins WHERE name = N'$(TENANT_DB_USER)')
BEGIN
    PRINT 'creating login $(TENANT_DB_USER)';
    EXEC('CREATE LOGIN [$(TENANT_DB_USER)] WITH PASSWORD = ''$(TENANT_DB_PASSWORD)''');
END
ELSE
BEGIN
    PRINT 'refreshing login $(TENANT_DB_USER) password';
    EXEC('ALTER LOGIN [$(TENANT_DB_USER)] WITH PASSWORD = ''$(TENANT_DB_PASSWORD)''');
END
GO

USE [$(TENANT_DB_NAME)];
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'$(TENANT_DB_USER)')
BEGIN
    PRINT 'creating database user $(TENANT_DB_USER)';
    EXEC('CREATE USER [$(TENANT_DB_USER)] FOR LOGIN [$(TENANT_DB_USER)]');
    EXEC('ALTER ROLE db_owner ADD MEMBER [$(TENANT_DB_USER)]');
END
ELSE
    PRINT 'database user $(TENANT_DB_USER) already exists';
GO
"""


class MetaBuilderJobError(Exception):
    pass


def _get_hub_api_client() -> client.ApiClient:
    """Tenant-operator runs on the hub itself, so this is always in-cluster
    config -- distinct from kubernetes_service._get_api_client(), which
    talks to spokes via the external kubeconfig this operator was handed."""
    k8s_config.load_incluster_config()
    return client.ApiClient()


def build_meta_builder_job(tenant: Tenant, db_secret: dict) -> dict:
    # slug (id-prefixed), not the bare tenant name, so this Job's name
    # stays consistent with the namespace/Vault path/git file/CR naming --
    # see Tenant.slug's docstring. The trailing timestamp still avoids
    # collisions between successive Jobs for the same tenant (retriggered
    # updates, etc), since Job names are immutable once created.
    job_name = f"meta-builder-{tenant.slug}-{int(time.time())}"[:63].rstrip("-")

    sqlcmd_args = [
        "-C", "-b", "-S", f"{settings.mssql_admin_host},{settings.mssql_admin_port}",
        "-U", settings.mssql_admin_user, "-P", "$(ADMIN_DB_PASSWORD)",
        "-v",
        f"TENANT_DB_NAME={db_secret.get('DB_NAME', '')}",
        f"TENANT_DB_USER={db_secret.get('DB_USER', '')}",
        f"TENANT_DB_PASSWORD={db_secret.get('DB_PASSWORD', '')}",
        "-Q", _SEED_SQL,
    ]

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": settings.meta_builder_job_namespace,
            "labels": {
                "app.kubernetes.io/component": "meta-builder",
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
                            "image": _SQLCMD_IMAGE,
                            "command": [_SQLCMD_BIN, *sqlcmd_args],
                            "env": [
                                {"name": "ADMIN_DB_PASSWORD", "value": settings.mssql_admin_password or ""},
                            ],
                        }
                    ],
                }
            },
        },
    }


class SeedJobError(Exception):
    """A DB seeding Job could not be submitted, failed, or timed out.
    provisioner.py turns this into a FAILED tenant instead of leaving it
    RUNNING with an empty database."""


def _job_outcome(job) -> str | None:
    """"succeeded", "failed", or None while still running/retrying.

    Uses the Job's terminal conditions, NOT status.failed: that field is a
    count of failed pods, which is already >0 after the first failed attempt
    even though backoffLimit may still retry it."""
    status = job.status
    for cond in status.conditions or []:
        if cond.status == "True" and cond.type == "Complete":
            return "succeeded"
        if cond.status == "True" and cond.type == "Failed":
            return "failed"
    if status.succeeded:
        return "succeeded"
    return None


def _poll_job_to_completion(
    api_client: client.ApiClient,
    job_name: str,
    namespace: str,
    label: str = "meta-builder",
    timeout_seconds: int | None = None,
) -> bool:
    """Shared polling core for the background watch (_watch_job) and the
    blocking wait helpers. Returns True only on a confirmed success; False
    on a terminal failure (retries exhausted) or timeout."""
    batch_v1 = client.BatchV1Api(api_client)
    timeout = timeout_seconds or _JOB_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            job = batch_v1.read_namespaced_job_status(job_name, namespace)
        except ApiException as e:
            logger.warning("[%s] job=%s status check failed (will retry): %s", label, job_name, e)
            time.sleep(_JOB_POLL_INTERVAL_SECONDS)
            continue

        outcome = _job_outcome(job)
        if outcome == "succeeded":
            logger.info("[%s] job=%s completed successfully", label, job_name)
            return True
        if outcome == "failed":
            logger.error(
                "[%s] job=%s failed (retries exhausted) -- check the Job's own pod logs "
                "(kubectl logs -n %s job/%s)",
                label, job_name, namespace, job_name,
            )
            return False
        time.sleep(_JOB_POLL_INTERVAL_SECONDS)

    logger.error("[%s] job=%s did not complete within %ds", label, job_name, timeout)
    return False


def wait_for_job(job_name: str, namespace: str | None = None) -> bool:
    """Blocking variant of the same wait, for callers that need to know
    this Job actually finished before doing something that depends on it
    -- e.g. bridge_meta_builder_job.py's Job connects to the tenant's
    database as the tenant's own login, which this Job is what creates in
    the first place, so provisioner.py needs this to actually complete
    (not just "submitted") before triggering that one."""
    api_client = _get_hub_api_client()
    return _poll_job_to_completion(api_client, job_name, namespace or settings.meta_builder_job_namespace)


def trigger_meta_builder_job(tenant: Tenant, admin_password: str) -> str | None:
    """Submits the real seeding Job for this tenant, if it has a
    controlops-server secret in Vault to seed. Returns the Job name, or
    None if there's nothing to seed (no controlops-server service, Vault
    disabled, or admin DB credentials aren't configured on this operator
    deployment -- logged, not raised, since a missing DB integration
    shouldn't fail an otherwise-successful tenant onboarding).
    """
    if not settings.mssql_admin_host or not settings.mssql_admin_password:
        logger.info(
            "[meta-builder] tenant=%s skipping DB seeding: mssql_admin_host/mssql_admin_password not configured",
            tenant.tenant_name,
        )
        return None

    db_secret = vault_service.read_qraie_bridge_tenant_secret(tenant.slug, _DB_SERVICE)
    if not db_secret or not db_secret.get("DB_NAME"):
        logger.info(
            "[meta-builder] tenant=%s skipping DB seeding: no '%s' secret in Vault (service not enabled for this tenant)",
            tenant.tenant_name, _DB_SERVICE,
        )
        return None

    job = build_meta_builder_job(tenant, db_secret)
    job_name = job["metadata"]["name"]
    namespace = settings.meta_builder_job_namespace

    logger.info(
        "[meta-builder] tenant=%s db ready -- submitting seeding Job '%s' in namespace '%s' on the hub",
        tenant.tenant_name, job_name, namespace,
    )

    api_client = _get_hub_api_client()
    batch_v1 = client.BatchV1Api(api_client)
    try:
        batch_v1.create_namespaced_job(namespace, job)
    except ApiException as e:
        logger.error("[meta-builder] tenant=%s failed to submit seeding Job: %s", tenant.tenant_name, e)
        raise SeedJobError(f"could not submit database seeding Job: {e.reason or e}") from e

    return job_name
