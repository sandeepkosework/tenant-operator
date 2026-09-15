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
import threading
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


def _watch_job(api_client: client.ApiClient, job_name: str, namespace: str) -> None:
    """Polls the Job to completion (or timeout/failure). Runs on a
    background thread -- must never raise past trigger_meta_builder_job()'s
    fire-and-forget call site; logs the outcome instead."""
    batch_v1 = client.BatchV1Api(api_client)
    deadline = time.monotonic() + _JOB_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        try:
            job = batch_v1.read_namespaced_job_status(job_name, namespace)
        except ApiException as e:
            logger.warning("[meta-builder] job=%s status check failed (will retry): %s", job_name, e)
            time.sleep(_JOB_POLL_INTERVAL_SECONDS)
            continue

        status = job.status
        if status.succeeded:
            logger.info("[meta-builder] job=%s completed successfully", job_name)
            return
        if status.failed:
            logger.error(
                "[meta-builder] job=%s failed -- tenant's DB credentials in Vault do NOT match a "
                "working login; check the Job's own pod logs (kubectl logs -n %s job/%s)",
                job_name, namespace, job_name,
            )
            return
        time.sleep(_JOB_POLL_INTERVAL_SECONDS)

    logger.error("[meta-builder] job=%s did not complete within %ds", job_name, _JOB_TIMEOUT_SECONDS)


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
        return None

    threading.Thread(target=_watch_job, args=(api_client, job_name, namespace), daemon=True).start()

    return job_name
