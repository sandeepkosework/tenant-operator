"""
Tenant custom resource on the hub cluster.

Before GitOps takes over (values.yaml commit -> Argo CD), the operator
records intent as a Tenant CR on the hub -- this is the object an operator
watching the hub would reconcile against. For this POC there is no real CRD
installed and no real API call is made: creation and status transitions are
built and logged/echoed so the shape and sequencing of the logic can be
verified before wiring in an actual Kubernetes client call.
"""
import logging
import uuid

import yaml

from app.config import get_settings
from app.models.tenant import Tenant
from app.services.cluster_selector import ClusterInfo

logger = logging.getLogger("tenant-operator.tenant_cr")
settings = get_settings()


def build_tenant_cr(tenant: Tenant, cluster: ClusterInfo) -> dict:
    return {
        "apiVersion": settings.cr_api_version,
        "kind": settings.cr_kind,
        "metadata": {
            # slug (id-prefixed), not the bare tenant name, so this CR's
            # name stays unique/scannable on the hub across thousands of
            # tenants the same way the namespace/Vault path/git file do --
            # see Tenant.slug's docstring.
            "name": tenant.slug,
            "namespace": settings.hub_namespace,
        },
        "spec": {
            # The real operator-allocated sequential id, not a duplicate of
            # the tenant name -- tenant_seq can be None only for rows
            # created before this column existed.
            "tenantId": tenant.tenant_seq,
            "tenantName": tenant.tenant_name,
            "domain": tenant.domain,
            "placement": {
                "cluster": cluster.name,
                "region": cluster.region,
            },
            "tier": tenant.tier or settings.default_tier,
            "storage": {
                "mssql": {
                    "shardId": f"mssql-shard-{cluster.name}",
                    "dbName": f"db_tenant_{tenant.tenant_name}",
                },
                "mongodb": {
                    "shardId": f"mongo-shard-{cluster.name}",
                    "collectionPrefix": f"t{tenant.tenant_name}_",
                },
            },
            "gateway": {
                "replicas": 3,
                "sessionAffinity": True,
            },
        },
    }


def create_tenant_cr(cr: dict) -> tuple[str, str]:
    """
    Echoes what `kubectl apply -f -` on the hub cluster would do. Returns a
    (name, uid) pair the way the real API server would after a create.
    POC only -- no Kubernetes API call is made.
    """
    name = cr["metadata"]["name"]
    namespace = cr["metadata"]["namespace"]
    cr_uid = str(uuid.uuid4())

    logger.info(
        "[hub-cr] creating %s/%s '%s' on hub cluster (echoed, not applied for real):\n%s",
        settings.cr_kind, namespace, name, yaml.safe_dump(cr, sort_keys=False),
    )
    print(f"kubectl apply -n {namespace} -f - <<EOF\n{yaml.safe_dump(cr, sort_keys=False)}EOF")

    return name, cr_uid


def echo_cr_phase(tenant: Tenant, phase: str) -> None:
    """Called on every tenant status transition so the CR's status is
    continuously reflected/monitored, mirroring what a real watch loop would do."""
    logger.info(
        "[hub-cr] %s/%s status -> phase=%s (tenant=%s, cluster=%s)",
        settings.hub_namespace, tenant.hub_cr_name or tenant.tenant_name, phase,
        tenant.tenant_name, tenant.cluster,
    )
