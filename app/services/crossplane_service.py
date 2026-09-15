"""
Crossplane integration for prod-only new-spoke OKE provisioning.

When a prod spoke crosses its scale-out threshold (spoke_scaler.py), the
operator applies a Crossplane claim on the HUB cluster requesting a new OKE
cluster, then polls its status until Crossplane reports it Ready. Crossplane
itself (running on the hub) takes it from there: provisions the OKE cluster,
runs whatever Composition steps you've defined (node pools, networking,
etc.), and writes connection details to a Secret.

This is deliberately scoped to *creating the claim and waiting for it* --
it does NOT join the new cluster to Argo CD or add it to clusters.yaml.
Those are still separate, deliberate steps (see the log line at the end of
a successful provision) since they touch source-controlled config and the
Argo CD cluster registry, not just cloud infrastructure.

Only used when settings.crossplane_enabled is True (prod deployments with a
real hub cluster + Crossplane installed). See spoke_scaler.py for the
log-only fallback used everywhere else (local/no-cluster testing, stage).
"""
import logging
import time

from kubernetes import client, config as k8s_config
from kubernetes.client import ApiException

from app.config import get_settings

logger = logging.getLogger("tenant-operator.crossplane_service")
settings = get_settings()

_hub_api_client: client.ApiClient | None = None


class CrossplaneServiceError(Exception):
    pass


def _get_hub_api_client() -> client.ApiClient:
    global _hub_api_client
    if _hub_api_client is not None:
        return _hub_api_client

    if settings.hub_kubeconfig_path:
        _hub_api_client = k8s_config.new_client_from_config(
            config_file=settings.hub_kubeconfig_path, context=settings.hub_kube_context
        )
    else:
        # In-cluster: correct when the operator runs on the hub itself.
        k8s_config.load_incluster_config()
        _hub_api_client = client.ApiClient()

    return _hub_api_client


def _group_version() -> tuple[str, str]:
    group, _, version = settings.crossplane_api_version.partition("/")
    if not version:
        raise CrossplaneServiceError(
            f"crossplane_api_version '{settings.crossplane_api_version}' must be '<group>/<version>'"
        )
    return group, version


def build_oke_claim(cluster_name: str, region: str | None = None, node_count: int | None = None) -> dict:
    claim: dict = {
        "apiVersion": settings.crossplane_api_version,
        "kind": settings.crossplane_kind,
        "metadata": {
            "name": cluster_name,
            "namespace": settings.crossplane_claim_namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "tenant-operator",
                "platform.saas.com/environment": "prod",
            },
        },
        "spec": {
            # NOTE: this shape is specific to your XRD/Composition -- adjust
            # to match whatever parameters your OKE Composition actually
            # accepts. This is a reasonable generic default, not a contract.
            "parameters": {
                "region": region or settings.crossplane_default_region,
                "nodeCount": node_count or settings.crossplane_default_node_count,
            },
            "writeConnectionSecretToRef": {
                "name": f"{cluster_name}-conn",
            },
        },
    }
    if settings.crossplane_composition_ref:
        claim["spec"]["compositionRef"] = {"name": settings.crossplane_composition_ref}
    return claim


def create_oke_claim(claim: dict) -> None:
    """Applies the claim to the hub cluster. Idempotent -- if it already
    exists (e.g. a retry after a partial failure), that's treated as success."""
    group, version = _group_version()
    api = client.CustomObjectsApi(_get_hub_api_client())
    try:
        api.create_namespaced_custom_object(
            group=group,
            version=version,
            namespace=settings.crossplane_claim_namespace,
            plural=settings.crossplane_plural,
            body=claim,
        )
    except ApiException as e:
        if e.status == 409:
            logger.info(
                "Crossplane claim '%s' already exists in namespace '%s' -- treating as already in progress",
                claim["metadata"]["name"], settings.crossplane_claim_namespace,
            )
            return
        raise CrossplaneServiceError(f"failed to create Crossplane claim: {e}") from e


def get_oke_claim_status(cluster_name: str) -> dict:
    """
    Returns {"ready": bool, "conditions": [...]} based on the claim's
    status.conditions -- Crossplane claims/XRs follow the standard
    Kubernetes condition convention (type=="Ready", status=="True").
    """
    group, version = _group_version()
    api = client.CustomObjectsApi(_get_hub_api_client())
    try:
        obj = api.get_namespaced_custom_object(
            group=group,
            version=version,
            namespace=settings.crossplane_claim_namespace,
            plural=settings.crossplane_plural,
            name=cluster_name,
        )
    except ApiException as e:
        if e.status == 404:
            return {"ready": False, "conditions": []}
        raise CrossplaneServiceError(f"failed to read Crossplane claim status: {e}") from e

    conditions = obj.get("status", {}).get("conditions", [])
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
    return {"ready": ready, "conditions": conditions}


def wait_for_oke_claim_ready(cluster_name: str, timeout: float | None = None, poll_interval: float | None = None) -> bool:
    """Blocks the calling thread until the claim is Ready or the timeout elapses."""
    timeout = timeout if timeout is not None else settings.crossplane_provision_timeout_seconds
    poll_interval = poll_interval if poll_interval is not None else settings.crossplane_poll_interval_seconds

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = get_oke_claim_status(cluster_name)
        if status["ready"]:
            return True
        time.sleep(poll_interval)

    return False
