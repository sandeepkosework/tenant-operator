"""
Pre-provisioning checks -- run before anything is created for a new tenant.

A tenant's SQL Server database/login is named after the bare tenant name
(no sequence number), and its MongoDB database is "<tenant_name>-bridge", so
two tenants created with the same tenantId end up on the same databases.
Tenant deletion removes the namespace but never drops those databases, so a
re-used tenantId would silently collide with the earlier tenant's data (the
schema script then dies on "table already exists", the login's password gets
reset under the old tenant, ...). This module refuses up front, with an
error that says exactly what already exists, instead of failing halfway
through provisioning.

Checked:
  - SQL Server: database and login named <tenant_name>
  - MongoDB:    database <tenant_name>-bridge has any collections
  - spoke:      namespace tenant-<tenant_name>-<n> (any sequence number)
                still exists, with the PVCs inside it, and any
                PersistentVolume still claimed from such a namespace

Every check that can't be completed (SQL Server unreachable, no permission
to list PVs, ...) is reported as "could not verify ..." rather than skipped
silently, except PV listing, where a 401/403 is only logged: not every
kubeconfig is allowed to read cluster-scoped objects, and the namespace/PVC
checks already cover the normal leftovers.

No Pod-log access is needed: the SQL check's Job writes its answer to the
container termination message, which is part of the Pod's status.

PREFLIGHT_ENABLED=false turns the whole thing off.
"""
import logging
import re
import time

from kubernetes import client
from kubernetes.client import ApiException

from app.config import get_settings
from app.models.tenant import Tenant
from app.services import kubernetes_service
from app.services.meta_builder_job import _SQLCMD_BIN, _SQLCMD_IMAGE, _get_hub_api_client

logger = logging.getLogger("tenant-operator.preflight")
settings = get_settings()

# tenantId is already validated to this shape at the API (RFC-1123 label);
# re-checked here because the value is spliced into a T-SQL string literal.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

_JOB_POLL_SECONDS = 3

# Runs two read-only queries and writes "DB=<n> LOGIN=<n>" to the container
# termination message. A query/connection failure writes SQL_CHECK_FAILED:...
# instead -- the script always exits 0 so the Job is Complete either way and
# the answer is read from the message, not from the exit status.
_SQL_CHECK_SCRIPT = r"""
q() { __SQLCMD__ -C -b -S "$SQL_HOST,$SQL_PORT" -U "$SQL_USER" -P "$ADMIN_DB_PASSWORD" -h -1 -W -Q "SET NOCOUNT ON; $1" 2>&1; }
num() { printf '%s' "$1" | tr -d ' \r' | grep -E '^[0-9]+$' | tail -n1; }
fail() { printf 'SQL_CHECK_FAILED: %s' "$(printf '%s' "$1" | tr '\n' ' ' | cut -c1-300)" > /dev/termination-log; exit 0; }
DB=$(q "SELECT COUNT(*) FROM sys.databases WHERE name = N'$CHECK_NAME'") || fail "$DB"
LG=$(q "SELECT COUNT(*) FROM sys.sql_logins WHERE name = N'$CHECK_NAME'") || fail "$LG"
printf 'DB=%s LOGIN=%s' "$(num "$DB")" "$(num "$LG")" > /dev/termination-log
""".replace("__SQLCMD__", _SQLCMD_BIN)


def _build_sql_check_job(job_name: str, tenant_name: str) -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": settings.meta_builder_job_namespace,
            "labels": {"app.kubernetes.io/component": "preflight", "tenant": tenant_name},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": settings.preflight_sql_timeout_seconds,
            "ttlSecondsAfterFinished": 600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "preflight",
                            "image": _SQLCMD_IMAGE,
                            "command": ["/bin/sh", "-c", _SQL_CHECK_SCRIPT],
                            "env": [
                                {"name": "SQL_HOST", "value": settings.mssql_admin_host or ""},
                                {"name": "SQL_PORT", "value": str(settings.mssql_admin_port)},
                                {"name": "SQL_USER", "value": settings.mssql_admin_user},
                                {"name": "ADMIN_DB_PASSWORD", "value": settings.mssql_admin_password or ""},
                                {"name": "CHECK_NAME", "value": tenant_name},
                            ],
                        }
                    ],
                }
            },
        },
    }


def _parse_sql_answer(message: str | None) -> tuple[int, int]:
    """Termination message -> (database_count, login_count). Raises
    ValueError with a readable reason if the check didn't produce an answer."""
    text = (message or "").strip()
    if not text:
        raise ValueError("the check Job produced no result")
    if text.startswith("SQL_CHECK_FAILED"):
        raise ValueError(text)
    m = re.fullmatch(r"DB=(\d+) LOGIN=(\d+)", text)
    if not m:
        raise ValueError(f"unexpected result '{text[:120]}'")
    return int(m.group(1)), int(m.group(2))


def _read_termination_message(core_v1: client.CoreV1Api, job_name: str, namespace: str) -> str | None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        pods = core_v1.list_namespaced_pod(namespace, label_selector=f"job-name={job_name}").items
        for pod in pods:
            for cs in (pod.status.container_statuses or []):
                term = cs.state.terminated if cs.state else None
                if term is not None:
                    return term.message
        time.sleep(1)
    return None


def _run_sql_check(tenant: Tenant) -> tuple[int, int]:
    api_client = _get_hub_api_client()
    batch_v1 = client.BatchV1Api(api_client)
    core_v1 = client.CoreV1Api(api_client)
    namespace = settings.meta_builder_job_namespace
    job_name = f"preflight-{tenant.slug}-{int(time.time())}"[:63].rstrip("-")

    batch_v1.create_namespaced_job(namespace, _build_sql_check_job(job_name, tenant.tenant_name))
    logger.info("[preflight] tenant=%s submitted SQL check Job '%s'", tenant.tenant_name, job_name)

    deadline = time.monotonic() + settings.preflight_sql_timeout_seconds + 30
    while time.monotonic() < deadline:
        status = batch_v1.read_namespaced_job_status(job_name, namespace).status
        if status.succeeded or status.failed:
            break
        time.sleep(_JOB_POLL_SECONDS)
    else:
        raise ValueError(f"the check Job '{job_name}' did not finish in time")

    return _parse_sql_answer(_read_termination_message(core_v1, job_name, namespace))


def _check_sql(tenant: Tenant) -> list[str]:
    if not settings.mssql_admin_host or not settings.mssql_admin_password:
        logger.info("[preflight] tenant=%s skipping SQL check: mssql admin connection not configured", tenant.tenant_name)
        return []
    name = tenant.tenant_name
    try:
        db_count, login_count = _run_sql_check(tenant)
    except (ValueError, ApiException) as e:
        return [f"could not verify SQL Server {settings.mssql_admin_host} ({e})"]
    problems = []
    if db_count:
        problems.append(f"SQL database '{name}' already exists on {settings.mssql_admin_host}")
    if login_count:
        problems.append(f"SQL login '{name}' already exists on {settings.mssql_admin_host}")
    return problems


def _check_mongo(tenant_name: str) -> list[str]:
    uri = settings.mongo_env_config_uri
    if not uri:
        return []
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    db_name = f"{tenant_name}-bridge"
    mongo = None
    try:
        mongo = MongoClient(uri, serverSelectionTimeoutMS=5000)
        collections = mongo[db_name].list_collection_names()
    except PyMongoError as e:
        return [f"could not verify MongoDB database '{db_name}' ({str(e)[:160]})"]
    finally:
        if mongo is not None:
            mongo.close()
    if collections:
        return [f"MongoDB database '{db_name}' already exists with {len(collections)} collection(s)"]
    return []


def _check_spoke(cluster_context: str, tenant_name: str) -> list[str]:
    core_v1 = client.CoreV1Api(kubernetes_service._get_api_client(cluster_context))
    pattern = re.compile(rf"^tenant-{re.escape(tenant_name)}-\d+$")
    problems: list[str] = []

    try:
        namespaces = sorted(n.metadata.name for n in core_v1.list_namespace().items if pattern.match(n.metadata.name))
    except ApiException as e:
        return [f"could not verify namespaces on cluster '{cluster_context}' ({e.status})"]

    for ns in namespaces:
        try:
            pvcs = [p.metadata.name for p in core_v1.list_namespaced_persistent_volume_claim(ns).items]
        except ApiException as e:
            pvcs = []
            problems.append(f"could not list PVCs in namespace '{ns}' ({e.status})")
        detail = f" with {len(pvcs)} PVC(s): {', '.join(pvcs[:5])}{'...' if len(pvcs) > 5 else ''}" if pvcs else ""
        problems.append(f"namespace '{ns}' already exists on cluster '{cluster_context}'{detail}")

    try:
        pvs = core_v1.list_persistent_volume().items
    except ApiException as e:
        if e.status in (401, 403):
            logger.warning("[preflight] not allowed to list PersistentVolumes on '%s' -- skipping the PV check", cluster_context)
            return problems
        return problems + [f"could not verify PersistentVolumes on cluster '{cluster_context}' ({e.status})"]

    claimed = sorted(
        pv.metadata.name for pv in pvs
        if pv.spec and pv.spec.claim_ref and pattern.match(pv.spec.claim_ref.namespace or "")
    )
    if claimed:
        problems.append(
            f"{len(claimed)} PersistentVolume(s) still claimed from namespace(s) tenant-{tenant_name}-<n>: "
            f"{', '.join(claimed[:5])}{'...' if len(claimed) > 5 else ''}"
        )
    return problems


def check_tenant_resources(tenant: Tenant, cluster_context: str) -> list[str]:
    """Returns a list of human-readable problems; empty means clear to provision."""
    if not settings.preflight_enabled:
        logger.info("[preflight] tenant=%s disabled (PREFLIGHT_ENABLED=false)", tenant.tenant_name)
        return []
    name = tenant.tenant_name
    if not _NAME_RE.match(name):
        return [f"tenant name '{name}' is not a valid lowercase DNS label"]

    problems: list[str] = []
    checks = (
        ("SQL Server", lambda: _check_sql(tenant)),
        ("MongoDB", lambda: _check_mongo(name)),
        ("spoke cluster", lambda: _check_spoke(cluster_context, name)),
    )
    for label, check in checks:
        try:
            problems.extend(check())
        except Exception as e:  # noqa: BLE001 -- a broken check must not look like "clear"
            logger.exception("[preflight] tenant=%s %s check crashed", name, label)
            problems.append(f"could not verify {label} ({e})")
    if problems:
        logger.error("[preflight] tenant=%s FAILED: %s", name, "; ".join(problems))
    else:
        logger.info("[preflight] tenant=%s clear: no existing database, login, namespace, PVC or PV", name)
    return problems
