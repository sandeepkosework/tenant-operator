"""
Optional purge of a deleted tenant's data stores.

A plain tenant delete (provisioner.delete_tenant) removes the GitOps manifest and
the namespace, so Argo CD prunes the workloads, but it deliberately leaves the
tenant's data behind: the SQL database and login, the Mongo databases, the Vault
secrets and the onboarding-server record. Because those stay, preflight refuses to
create a tenant of the same name until they are removed by hand.

This module removes them, only when asked (DELETE /tenant/{id}?purge=true or
POST /tenant/{id}/purge) and only if PURGE_ENABLED=true and the tenant's
environment is in PURGE_ALLOWED_ENVIRONMENTS. Steps, in order:

  1. onboarding-server record   DELETE <ONBOARDING_SERVER_URL>/config/<tenant name>
  2. Mongo databases            "<tenant name>-bridge" and the env-config mirror
  3. SQL database + login       a Job on the hub, same pattern as preflight
  4. Vault secrets              secret/<prefix>/<slug>/*, every version

Each step runs on its own: a failure is reported and the next step still runs, and
every step is safe to repeat. Nothing is touched unless the tenant is DELETED and no
other, live tenant uses the same name (otherwise the data would be the new tenant's).
Not removed on purpose: the Postgres row (soft-deleted), the registry record (marked
DELETED) and the tenant-info file in git, which are the audit trail.

The hub "Tenant" CR is not a step: create_tenant_cr() only echoes it (no CRD is
installed), so there is nothing to delete.
"""
import logging
import re
import threading
import time

from kubernetes import client
from kubernetes.client import ApiException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.tenant import Tenant, TenantStatus
from app.services import mongo_service, onboarding_service, vault_service
from app.services.meta_builder_job import _SQLCMD_BIN, _SQLCMD_IMAGE, _get_hub_api_client
from app.services.preflight import _JOB_POLL_SECONDS, _NAME_RE, _read_termination_message, _run_sql_check

logger = logging.getLogger("tenant-operator.purge")
settings = get_settings()


class PurgeNotAllowed(Exception):
    """Raised when a purge must not run. `code` says why (disabled, environment,
    confirm, state, name, namesake) so the API can answer with the right status."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- one purge at a time per tenant ----------------------------------------------

_running: set[str] = set()
_running_lock = threading.Lock()


def is_running(tenant_id) -> bool:
    with _running_lock:
        return str(tenant_id) in _running


def begin(tenant_id) -> bool:
    """True if this call took the slot, False if a purge for the tenant is already running."""
    with _running_lock:
        if str(tenant_id) in _running:
            return False
        _running.add(str(tenant_id))
        return True


def end(tenant_id) -> None:
    with _running_lock:
        _running.discard(str(tenant_id))


# --- guards ------------------------------------------------------------------------

def check_purge_allowed(tenant: Tenant, confirm: str | None) -> None:
    """Request-level guards for a real purge (not a dry run)."""
    if not settings.purge_enabled:
        raise PurgeNotAllowed("disabled", "purging tenant data is turned off on this operator (PURGE_ENABLED=false)")
    if tenant.environment not in settings.purge_allowed_environments:
        raise PurgeNotAllowed(
            "environment",
            f"purging is not allowed for environment '{tenant.environment}' "
            f"(PURGE_ALLOWED_ENVIRONMENTS={settings.purge_allowed_environments})",
        )
    if confirm != tenant.tenant_name:
        raise PurgeNotAllowed("confirm", "confirm must equal the tenant name")


def check_purgeable(db: Session, tenant: Tenant) -> None:
    """State guards, for dry runs and real purges alike."""
    if tenant.status != TenantStatus.DELETED:
        raise PurgeNotAllowed("state", f"the tenant is {tenant.status.value}; only a DELETED tenant can be purged")
    if not _NAME_RE.fullmatch(tenant.tenant_name or ""):
        raise PurgeNotAllowed("name", f"'{tenant.tenant_name}' is not a valid tenant name for a purge")
    # A newer tenant may have reused this name; its data lives in the very same databases.
    live = (
        db.query(Tenant)
        .filter(Tenant.tenant_name == tenant.tenant_name, Tenant.id != tenant.id, Tenant.status != TenantStatus.DELETED)
        .first()
    )
    if live is not None:
        raise PurgeNotAllowed(
            "namesake",
            f"another tenant named '{tenant.tenant_name}' exists ({live.status.value}); "
            "purging would remove its data",
        )


# --- SQL Job ------------------------------------------------------------------------

# Drops the database (single-user first, so open connections do not block it) and the
# login, then writes "DB_DROPPED=<n> LOGIN_DROPPED=<n>" to the termination message,
# where n is 1 if it existed. Any failure writes SQL_PURGE_FAILED:... and the script
# still exits 0, so the answer is read from the message, as preflight does. Dropping a
# database drops the user inside it.
_SQL_PURGE_SCRIPT = r"""
q() { __SQLCMD__ -C -b -S "$SQL_HOST,$SQL_PORT" -U "$SQL_USER" -P "$ADMIN_DB_PASSWORD" -h -1 -W -Q "SET NOCOUNT ON; $1" 2>&1; }
num() { printf '%s' "$1" | tr -d ' \r' | grep -E '^[0-9]+$' | tail -n1; }
fail() { printf 'SQL_PURGE_FAILED: %s' "$(printf '%s' "$1" | tr '\n' ' ' | cut -c1-300)" > /dev/termination-log; exit 0; }
DB=$(q "SELECT COUNT(*) FROM sys.databases WHERE name = N'$PURGE_NAME'") || fail "$DB"
if [ "$(num "$DB")" != "0" ]; then
  OUT=$(q "ALTER DATABASE [$PURGE_NAME] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [$PURGE_NAME];") || {
    q "ALTER DATABASE [$PURGE_NAME] SET MULTI_USER" > /dev/null
    fail "$OUT"
  }
fi
LG=$(q "SELECT COUNT(*) FROM sys.sql_logins WHERE name = N'$PURGE_NAME'") || fail "$LG"
if [ "$(num "$LG")" != "0" ]; then
  OUT=$(q "DROP LOGIN [$PURGE_NAME];") || fail "$OUT"
fi
printf 'DB_DROPPED=%s LOGIN_DROPPED=%s' "$(num "$DB")" "$(num "$LG")" > /dev/termination-log
""".replace("__SQLCMD__", _SQLCMD_BIN)


def _build_sql_purge_job(job_name: str, tenant_name: str) -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": settings.meta_builder_job_namespace,
            "labels": {"app.kubernetes.io/component": "purge", "tenant": tenant_name},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": settings.purge_sql_timeout_seconds,
            "ttlSecondsAfterFinished": 600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "purge",
                            "image": _SQLCMD_IMAGE,
                            "command": ["/bin/sh", "-c", _SQL_PURGE_SCRIPT],
                            "env": [
                                {"name": "SQL_HOST", "value": settings.mssql_admin_host or ""},
                                {"name": "SQL_PORT", "value": str(settings.mssql_admin_port)},
                                {"name": "SQL_USER", "value": settings.mssql_admin_user},
                                {"name": "ADMIN_DB_PASSWORD", "value": settings.mssql_admin_password or ""},
                                {"name": "PURGE_NAME", "value": tenant_name},
                            ],
                        }
                    ],
                }
            },
        },
    }


def _parse_sql_purge_answer(message: str | None) -> tuple[int, int]:
    """Termination message -> (databases dropped, logins dropped), or ValueError."""
    text = (message or "").strip()
    if not text:
        raise ValueError("the purge Job produced no result")
    if text.startswith("SQL_PURGE_FAILED"):
        raise ValueError(text)
    m = re.fullmatch(r"DB_DROPPED=(\d+) LOGIN_DROPPED=(\d+)", text)
    if not m:
        raise ValueError(f"unexpected result '{text[:120]}'")
    return int(m.group(1)), int(m.group(2))


def _run_sql_purge(tenant: Tenant) -> tuple[int, int]:
    api_client = _get_hub_api_client()
    batch_v1 = client.BatchV1Api(api_client)
    core_v1 = client.CoreV1Api(api_client)
    namespace = settings.meta_builder_job_namespace
    job_name = f"purge-{tenant.slug}-{int(time.time())}"[:63].rstrip("-")

    batch_v1.create_namespaced_job(namespace, _build_sql_purge_job(job_name, tenant.tenant_name))
    logger.info("[purge] tenant=%s submitted SQL purge Job '%s'", tenant.tenant_name, job_name)

    deadline = time.monotonic() + settings.purge_sql_timeout_seconds + 30
    while time.monotonic() < deadline:
        status = batch_v1.read_namespaced_job_status(job_name, namespace).status
        if status.succeeded or status.failed:
            break
        time.sleep(_JOB_POLL_SECONDS)
    else:
        raise ValueError(f"the purge Job '{job_name}' did not finish in time")

    return _parse_sql_purge_answer(_read_termination_message(core_v1, job_name, namespace))


# --- steps ----------------------------------------------------------------------------

def _result(step: str, status: str, detail: str) -> dict:
    return {"step": step, "status": status, "detail": detail}


def _step_onboarding(tenant: Tenant, dry_run: bool) -> dict:
    outcome = onboarding_service.delete_tenant_record(tenant, dry_run=dry_run)
    if outcome == "skipped":
        return _result("onboarding record", "skipped", "ONBOARDING_SERVER_URL is not set")
    status = {"deleted": "done", "would_delete": "would_do", "absent": "absent"}[outcome]
    return _result("onboarding record", status, f"{tenant.tenant_name}: {outcome.replace('_', ' ')}")


def _step_mongo(tenant: Tenant, dry_run: bool) -> dict:
    if not settings.mongo_env_config_uri:
        return _result("mongo databases", "skipped", "MONGO_ENV_CONFIG_URI is not set")
    items = mongo_service.purge_tenant_databases(tenant.tenant_name, tenant.slug, dry_run=dry_run)
    detail = "; ".join(
        f"{i['database']}: {i['action'].replace('_', ' ')}"
        + (f" ({i['collections']} collections)" if "collections" in i else "")
        + (f" ({i['documents']} documents)" if "documents" in i else "")
        + (f" -- {i['reason']}" if "reason" in i else "")
        for i in items
    )
    actions = {i["action"] for i in items}
    if actions & {"dropped", "removed"}:
        status = "done"
    elif actions & {"would_drop", "would_remove"}:
        status = "would_do"
    elif "absent" in actions:
        status = "absent"
    else:
        status = "skipped"
    return _result("mongo databases", status, detail)


def _step_sql(tenant: Tenant, dry_run: bool) -> dict:
    if not settings.mssql_admin_host or not settings.mssql_admin_password:
        return _result("sql database and login", "skipped", "the SQL admin connection is not configured")
    name = tenant.tenant_name
    if dry_run:
        try:
            db_count, login_count = _run_sql_check(tenant)
        except (ValueError, ApiException) as e:
            return _result("sql database and login", "failed", f"could not check SQL Server: {e}")
        status = "would_do" if (db_count or login_count) else "absent"
        return _result("sql database and login", status,
                       f"database '{name}': {'exists' if db_count else 'absent'}; login '{name}': {'exists' if login_count else 'absent'}")
    try:
        dropped_db, dropped_login = _run_sql_purge(tenant)
    except (ValueError, ApiException) as e:
        return _result("sql database and login", "failed", str(e)[:300])
    status = "done" if (dropped_db or dropped_login) else "absent"
    return _result("sql database and login", status,
                   f"database '{name}': {'dropped' if dropped_db else 'absent'}; login '{name}': {'dropped' if dropped_login else 'absent'}")


def _step_vault(tenant: Tenant, dry_run: bool) -> dict:
    if not settings.vault_enabled:
        return _result("vault secrets", "skipped", "VAULT_ENABLED=false")
    paths = vault_service.delete_tenant_secrets(tenant.slug, dry_run=dry_run)
    if not paths:
        return _result("vault secrets", "absent", f"nothing under {settings.vault_tenant_secret_prefix}/{tenant.slug}")
    return _result("vault secrets", "would_do" if dry_run else "done",
                   f"{len(paths)} secret(s) under {settings.vault_tenant_secret_prefix}/{tenant.slug}")


_STEPS = [
    ("onboarding record", _step_onboarding),
    ("mongo databases", _step_mongo),
    ("sql database and login", _step_sql),
    ("vault secrets", _step_vault),
]


def purge_tenant_data(tenant: Tenant, dry_run: bool = False) -> list[dict]:
    """Runs every purge step and returns one {step, status, detail} per step. A step
    that raises is reported as failed and does not stop the others. The caller has
    already run check_purgeable() (and check_purge_allowed() for a real purge)."""
    results = []
    for name, step in _STEPS:
        try:
            result = step(tenant, dry_run)
        except Exception as e:  # noqa: BLE001 -- one broken store must not stop the others
            logger.exception("[purge] tenant=%s step '%s' failed", tenant.tenant_name, name)
            result = _result(name, "failed", f"{type(e).__name__}: {str(e)[:250]}")
        logger.info("[purge] tenant=%s%s %s: %s -- %s", tenant.tenant_name, " (dry run)" if dry_run else "",
                    name, result["status"], result["detail"])
        results.append(result)
    return results
