"""
Runs the tenant-operator with NO Kubernetes cluster, NO Argo CD, and NO kind
required -- just Python + git.

It monkeypatches the two modules that would otherwise need a real cluster
(argocd_service, kubernetes_service) so every sync/health check succeeds
instantly. Every other code path is the real thing: FastAPI, the DB row,
capacity/scale-out logic, the Tenant CR echo, the git commit, and the
meta-builder Job echo.

Usage (from the tenant-operator/ directory):
    cp poc/.env.nocluster.example .env
    # edit .env: set GIT_REPO_URL to the path printed by
    # poc/init_local_gitops_repo.sh
    python poc/run_local_no_cluster.py

To exercise Argo CD failure handling (provisioner.py's fail-fast path)
against this same running server, set one of these before starting (or
edit .env and restart):
    SIMULATE_ARGOCD_UNREACHABLE=true              # every check raises ArgoCDUnreachableError
    SIMULATE_ARGOCD_FAILURE_TENANTS=acme,globex   # these tenants report a definitive Argo failure
Both default to "off" (everything reports Healthy/Synced), which is what
every other local guide/test in this repo assumes.
"""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

# Make `app` importable regardless of the caller's cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import argocd_service, kubernetes_service  # noqa: E402

_SIMULATE_UNREACHABLE = os.environ.get("SIMULATE_ARGOCD_UNREACHABLE", "false").lower() == "true"
_SIMULATE_FAILURE_TENANTS = {
    t.strip() for t in os.environ.get("SIMULATE_ARGOCD_FAILURE_TENANTS", "").split(",") if t.strip()
}

_HEALTHY_STATUS = {
    "exists": True,
    "health": "Healthy",
    "sync": "Synced",
    "operationPhase": "Succeeded",
    "message": None,
    "resources": [],
}
_FAILURE_STATUS = {
    "exists": True,
    "health": "Degraded",
    "sync": "OutOfSync",
    "operationPhase": "Failed",
    "message": "simulated failure (SIMULATE_ARGOCD_FAILURE_TENANTS)",
    "resources": [],
}


def _local_get_application_status(tenant_name: str) -> dict:
    if _SIMULATE_UNREACHABLE:
        raise argocd_service.ArgoCDUnreachableError("simulated outage (SIMULATE_ARGOCD_UNREACHABLE=true)")
    if tenant_name in _SIMULATE_FAILURE_TENANTS:
        return dict(_FAILURE_STATUS)
    return dict(_HEALTHY_STATUS)


def _local_is_fully_ready(cluster_context: str, namespace: str, tenant_name: str) -> bool:
    return True


def _local_namespace_fully_deleted(cluster_context: str, namespace: str) -> bool:
    return True


def _local_delete_namespace(cluster_context: str, namespace: str) -> None:
    logging.getLogger("poc.run_local_no_cluster").info(
        "local delete_namespace('%s') -- no real cluster, nothing to delete", namespace,
    )


argocd_service.get_application_status = _local_get_application_status
kubernetes_service.is_fully_ready = _local_is_fully_ready
kubernetes_service.namespace_fully_deleted = _local_namespace_fully_deleted
kubernetes_service.delete_namespace = _local_delete_namespace

logging.getLogger("poc.run_local_no_cluster").info(
    "local Argo CD simulation: unreachable=%s failure_tenants=%s",
    _SIMULATE_UNREACHABLE, sorted(_SIMULATE_FAILURE_TENANTS) or "(none)",
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
