"""
SpokeCluster custom resource on the hub cluster.

Alongside the per-tenant Tenant CR (see tenant_cr.py), the operator also
keeps one SpokeCluster CR per registered spoke, recording which tenants
currently live there and the count against each environment's capacity.
This is what a platform operator would look at to answer "what's on
spoke-1 right now?" without querying tenant-operator's own MongoDB directly.

POC note: same as tenant_cr.py -- no real CRD is installed and no real API
call is made. upsert_spoke_cluster_cr() builds the object from live
tenant-operator MongoDB state and logs/echoes the equivalent `kubectl apply`.
"""
import logging

import yaml
from pymongo.database import Database

from app.config import get_settings
from app.models.tenant import Tenant
from app.services import tenant_repo
from app.services.cluster_selector import ACTIVE_STATUSES, ClusterInfo

logger = logging.getLogger("tenant-operator.spoke_cr")
settings = get_settings()


def _active_tenants_for_cluster(cluster_name: str, db: Database) -> list[Tenant]:
    return tenant_repo.list_active_by_cluster(db, cluster_name, ACTIVE_STATUSES)


def build_spoke_cluster_cr(cluster: ClusterInfo, tenants: list[Tenant]) -> dict:
    capacity_by_environment = {}
    for env in cluster.environments:
        count = sum(1 for t in tenants if t.environment == env)
        capacity_by_environment[env] = {
            "count": count,
            "capacity": min(cluster.max_tenants, settings.spoke_capacity(env)),
        }

    return {
        "apiVersion": settings.cr_api_version,
        "kind": "SpokeCluster",
        "metadata": {
            "name": cluster.name,
            "namespace": settings.hub_namespace,
        },
        "spec": {
            "context": cluster.context,
            "region": cluster.region,
            "environments": cluster.environments,
        },
        "status": {
            "tenantCount": len(tenants),
            "capacityByEnvironment": capacity_by_environment,
            "tenants": [
                {"name": t.tenant_name, "environment": t.environment, "status": t.status.value}
                for t in tenants
            ],
        },
    }


def get_spoke_cluster_status(cluster: ClusterInfo, db: Database) -> dict:
    """Read-only: current CR content computed live from tenant-operator's
    own MongoDB, no echo/log."""
    return build_spoke_cluster_cr(cluster, _active_tenants_for_cluster(cluster.name, db))


def upsert_spoke_cluster_cr(cluster: ClusterInfo, db: Database) -> dict:
    """Call whenever a tenant is placed on or removed from `cluster` so the
    CR's tenant list/count stays current. Echoes `kubectl apply`; no real
    Kubernetes call is made."""
    cr = build_spoke_cluster_cr(cluster, _active_tenants_for_cluster(cluster.name, db))

    logger.info(
        "[hub-cr] upserting SpokeCluster/%s '%s' on hub cluster (echoed, not applied for real): "
        "tenantCount=%d",
        settings.hub_namespace, cluster.name, cr["status"]["tenantCount"],
    )
    print(f"kubectl apply -n {settings.hub_namespace} -f - <<EOF\n{yaml.safe_dump(cr, sort_keys=False)}EOF")

    return cr
