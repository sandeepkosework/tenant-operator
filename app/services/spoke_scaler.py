"""
Capacity-threshold logic for spoke clusters.

- prod:  1000 tenants/spoke. At 995 tenants placed on a spoke, kick off
  provisioning of a new spoke OKE cluster as a PARALLEL, fire-and-forget job
  so it never blocks the tenant currently being created. When
  settings.crossplane_enabled is True, this applies a real Crossplane claim
  on the hub cluster (see crossplane_service.py); otherwise it falls back to
  a log-only simulation, which is what local/no-cluster testing uses.
- stage: 3 tenants/spoke. At 2 tenants placed on a spoke, there is no
  autoscaling in stage -- surface a notification for an operator to
  provision the next spoke manually instead of doing it automatically.
  Crossplane is intentionally NOT used here, per your requirement that it's
  prod-only.
"""
import logging
import re
import threading
import time
import uuid

from app.config import get_settings
from app.services import cluster_selector, crossplane_service, notifications

logger = logging.getLogger("tenant-operator.spoke_scaler")
settings = get_settings()

# Guards against re-triggering a new-spoke job on every subsequent tenant
# placed on a spoke that has already crossed the threshold. POC-only,
# in-memory, single-process -- a real implementation would track this in the
# cluster registry/DB instead.
_scale_triggered: set[str] = set()


def _next_spoke_name(environment: str) -> str:
    """spoke-prod-1, spoke-prod-2, ... -> next unused sequential name for
    THIS environment only. Only ever called for prod (Crossplane path is
    prod-only), but scoped by environment regardless so a future stage
    fleet's numbering can never collide with prod's -- stage and prod are
    entirely separate spoke fleets, never sharing a cluster or a name."""
    prefix = f"spoke-{environment}-"
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    max_n = 0
    for c in cluster_selector.load_cluster_registry():
        if environment not in c.environments:
            continue
        m = pattern.match(c.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}{max_n + 1}"


def _simulate_oke_spoke_provisioning(cluster_name: str, environment: str) -> None:
    """Log-only fallback, used when crossplane_enabled is False (local/no-cluster
    testing, or prod before Crossplane is wired up). Runs on a background
    thread -- must never block the caller."""
    job_id = str(uuid.uuid4())[:8]
    steps = [
        "requesting new OKE node pool",
        "waiting for control plane to become ACTIVE",
        "joining new spoke to the hub cluster",
        "registering new spoke as an Argo CD cluster secret",
        "adding new spoke to this environment's clusters registry",
    ]
    logger.info(
        "[spoke-scale-out job=%s] starting parallel provisioning of a new spoke "
        "OKE cluster (triggered by '%s' in environment=%s)",
        job_id, cluster_name, environment,
    )
    for step in steps:
        logger.info("[spoke-scale-out job=%s] %s...", job_id, step)
        time.sleep(0.2)  # POC: simulate work without blocking tenant creation
    logger.info("[spoke-scale-out job=%s] new spoke provisioning complete (echoed, no real OKE call made)", job_id)


def _provision_new_spoke_via_crossplane(triggering_cluster: str, environment: str) -> None:
    """Real path: applies a Crossplane claim on the hub cluster and waits for
    it to become Ready. Runs on a background thread -- must never block the
    caller. Only reachable when settings.crossplane_enabled is True."""
    new_spoke_name = _next_spoke_name(environment)
    logger.info(
        "[spoke-scale-out] '%s' crossed its capacity threshold (environment=%s) -- "
        "requesting new spoke '%s' via Crossplane",
        triggering_cluster, environment, new_spoke_name,
    )
    try:
        claim = crossplane_service.build_oke_claim(new_spoke_name)
        crossplane_service.create_oke_claim(claim)
        logger.info(
            "[spoke-scale-out] Crossplane claim '%s/%s' applied on hub cluster; waiting up to %ss for it "
            "to become Ready...",
            settings.crossplane_claim_namespace, new_spoke_name, settings.crossplane_provision_timeout_seconds,
        )
        ready = crossplane_service.wait_for_oke_claim_ready(new_spoke_name)
    except crossplane_service.CrossplaneServiceError as e:
        logger.exception("[spoke-scale-out] Crossplane provisioning failed for new spoke '%s'", new_spoke_name)
        notifications.notify(
            event="spoke_provisioning_failed",
            message=f"Crossplane provisioning failed for new spoke '{new_spoke_name}': {e}",
            cluster=new_spoke_name, triggering_cluster=triggering_cluster, environment=environment,
        )
        return

    if ready:
        logger.info(
            "[spoke-scale-out] new spoke '%s' is Ready. Remaining manual steps: register it with Argo CD "
            "(`argocd cluster add <context>`) and add it to this environment's clusters registry -- "
            "neither is done automatically.",
            new_spoke_name,
        )
    else:
        notifications.notify(
            event="spoke_provisioning_timed_out",
            message=(
                f"Crossplane claim '{new_spoke_name}' did not become Ready within "
                f"{settings.crossplane_provision_timeout_seconds}s -- check its status on the hub cluster."
            ),
            cluster=new_spoke_name, triggering_cluster=triggering_cluster, environment=environment,
        )


def maybe_trigger_spoke_scale(cluster_name: str, environment: str, projected_count: int) -> None:
    """
    Call right after a tenant is placed on `cluster_name`, passing the
    tenant count on that cluster INCLUDING the tenant just placed.
    """
    trigger_at = settings.spoke_scale_trigger(environment)
    if projected_count < trigger_at:
        return

    if cluster_name in _scale_triggered:
        logger.debug(
            "spoke '%s' already past scale-out threshold (%s/%s tenants); skipping duplicate trigger",
            cluster_name, projected_count, trigger_at,
        )
        return
    _scale_triggered.add(cluster_name)

    if settings.autoscale_enabled(environment):
        logger.info(
            "spoke '%s' reached %s/%s tenants (environment=%s) -- launching parallel new-spoke provisioning job "
            "(crossplane_enabled=%s)",
            cluster_name, projected_count, trigger_at, environment, settings.crossplane_enabled,
        )
        target = _provision_new_spoke_via_crossplane if settings.crossplane_enabled else _simulate_oke_spoke_provisioning
        threading.Thread(target=target, args=(cluster_name, environment), daemon=True).start()
    else:
        notifications.notify(
            event="spoke_capacity_threshold_reached",
            message=(
                f"spoke '{cluster_name}' reached {projected_count}/{trigger_at} tenants in "
                f"environment='{environment}'. Stage has no autoscaling -- a new spoke must be "
                f"provisioned manually."
            ),
            cluster=cluster_name,
            environment=environment,
            tenant_count=projected_count,
            threshold=trigger_at,
        )
