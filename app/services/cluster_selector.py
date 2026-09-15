"""
Picks which spoke cluster a new tenant lands on.

Strategy: sequential fill. Clusters are tried in the order they appear in
the registry (clusters.yaml) and a tenant lands on the first one that isn't
yet at capacity for its environment -- so spoke-1 fills up completely before
spoke-2 ever receives a tenant, and so on. This is what makes the capacity
scale-out/notify thresholds (see spoke_scaler.py) mean "this spoke is about
to run out," rather than "some spoke somewhere is about to run out." Uses
live counts from tenant-operator's own MongoDB (source of truth for "which
tenants are active"), not from Kubernetes.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from pymongo.database import Database

from app.config import get_settings
from app.models.tenant import TenantStatus
from app.services import tenant_repo

settings = get_settings()

ACTIVE_STATUSES = (
    TenantStatus.PENDING,
    TenantStatus.VALIDATING,
    TenantStatus.GIT_COMMITTED,
    TenantStatus.SYNCING,
    TenantStatus.RUNNING,
    TenantStatus.UPDATING,
)


@dataclass
class ClusterInfo:
    name: str
    context: str
    argocd_cluster_server: str
    max_tenants: int
    environments: list[str]
    region: str = "us-east-1"
    # Only set for hub/spoke splits where Vault runs on a different cluster
    # than this one's tenant workloads -- the chart's own in-cluster DNS
    # default (vault.vault.svc) only resolves on whichever cluster Vault
    # itself runs on. None means "leave the chart's own default alone"
    # (single-cluster deployments, or Vault reachable via in-cluster DNS).
    vault_server: str | None = None
    # Only set when this cluster's ingress-nginx controller was installed
    # with a non-default --ingress-class (its IngressClass name won't be
    # the chart's generic "nginx" default). None means "leave the chart's
    # own default alone".
    ingress_class_name: str | None = None


class NoAvailableClusterError(Exception):
    """Raised when every eligible spoke is at capacity.

    This is intentionally NOT auto-remediated by spinning up a new spoke:
    provisioning a new Kubernetes cluster, joining it to the hub, and
    registering it with Argo CD is an infra change with real cost/security
    implications, so it's surfaced to operators instead of done silently.
    """


def load_cluster_registry() -> list[ClusterInfo]:
    path = Path(settings.clusters_config_path)
    if not path.is_absolute():
        # resolve relative to project root
        path = Path(__file__).resolve().parents[2] / settings.clusters_config_path
    data = yaml.safe_load(path.read_text())
    return [ClusterInfo(**c) for c in data["clusters"]]


def select_cluster(environment: str, db: Database) -> tuple[ClusterInfo, int]:
    """
    Returns (chosen_cluster, projected_tenant_count) where projected_tenant_count
    includes the tenant currently being placed -- callers use it to decide whether
    this placement just crossed the scale-out/notify threshold for the environment.
    """
    env_capacity = settings.spoke_capacity(environment)

    clusters = [c for c in load_cluster_registry() if environment in c.environments]
    if not clusters:
        raise NoAvailableClusterError(f"no cluster configured for environment '{environment}'")

    counts = tenant_repo.count_active_by_cluster(db, ACTIVE_STATUSES)

    # Sequential fill: clusters are tried in registry order (NOT sorted by
    # load) -- the first one under capacity wins, so earlier spokes fill up
    # completely before later ones are used at all.
    for c in clusters:
        cap = min(c.max_tenants, env_capacity)
        count = counts.get(c.name, 0)
        if count < cap:
            return c, count + 1

    raise NoAvailableClusterError(
        f"all clusters eligible for environment '{environment}' are at capacity: "
        f"{[(c.name, counts.get(c.name, 0)) for c in clusters]}"
    )
