"""
Read-only Kubernetes access: namespace existence, deployment/pod health,
PVC binding, ingress readiness, and post-delete verification.

This module must NEVER call create/patch/apply against the API server --
that would bypass GitOps and is exactly what the doc's architecture rules
out. It only reads, with one deliberate exception: delete_namespace() --
see its docstring.
"""
from kubernetes import client, config as k8s_config
from kubernetes.client import ApiException

from app.config import get_settings

settings = get_settings()

_clients: dict[str, client.ApiClient] = {}


class KubernetesServiceError(Exception):
    pass


def _get_api_client(cluster_context: str) -> client.ApiClient:
    """Cache one ApiClient per cluster context; contexts come from the shared kubeconfig."""
    if cluster_context in _clients:
        return _clients[cluster_context]

    if settings.kubeconfig_path:
        api_client = k8s_config.new_client_from_config(
            config_file=settings.kubeconfig_path, context=cluster_context
        )
    else:
        # in-cluster: only viable if operator runs on the target cluster itself,
        # which won't be true for spokes -- kubeconfig_path is expected in prod.
        k8s_config.load_incluster_config()
        api_client = client.ApiClient()

    _clients[cluster_context] = api_client
    return api_client


def namespace_exists(cluster_context: str, namespace: str) -> bool:
    v1 = client.CoreV1Api(_get_api_client(cluster_context))
    try:
        v1.read_namespace(namespace)
        return True
    except ApiException as e:
        if e.status == 404:
            return False
        raise KubernetesServiceError(str(e)) from e


def deployment_health(cluster_context: str, namespace: str, instance_label: str) -> dict:
    """
    Aggregates the checks called out in the doc's "Step 9": deployment
    ready, pods ready, PVC bound, ingress ready. Assumes the Helm chart
    labels resources with app.kubernetes.io/instance=<instance_label> --
    the Argo CD Application/Helm release name, i.e. tenant.slug, since
    that's what the ApplicationSet's git-directory generator names the
    release after (see Tenant.slug's docstring) -- NOT the bare tenant
    name.
    """
    apps_v1 = client.AppsV1Api(_get_api_client(cluster_context))
    core_v1 = client.CoreV1Api(_get_api_client(cluster_context))
    networking_v1 = client.NetworkingV1Api(_get_api_client(cluster_context))

    label_selector = f"app.kubernetes.io/instance={instance_label}"
    result = {
        "namespace_exists": False,
        "deployment_ready": False,
        "pods_ready": False,
        "pvc_bound": False,
        "ingress_ready": False,
        "detail": {},
    }

    if not namespace_exists(cluster_context, namespace):
        return result
    result["namespace_exists"] = True

    try:
        deployments = apps_v1.list_namespaced_deployment(namespace, label_selector=label_selector)
        if deployments.items:
            d = deployments.items[0]
            desired = d.spec.replicas or 0
            ready = d.status.ready_replicas or 0
            result["deployment_ready"] = desired > 0 and ready == desired
            result["detail"]["replicas"] = f"{ready}/{desired}"
    except ApiException as e:
        raise KubernetesServiceError(f"deployment check failed: {e}") from e

    try:
        pods = core_v1.list_namespaced_pod(namespace, label_selector=label_selector)
        if pods.items:
            result["pods_ready"] = all(
                cond.type == "Ready" and cond.status == "True"
                for p in pods.items
                for cond in (p.status.conditions or [])
                if cond.type == "Ready"
            ) and len(pods.items) > 0
    except ApiException as e:
        raise KubernetesServiceError(f"pod check failed: {e}") from e

    try:
        pvcs = core_v1.list_namespaced_persistent_volume_claim(namespace, label_selector=label_selector)
        result["pvc_bound"] = all(p.status.phase == "Bound" for p in pvcs.items) if pvcs.items else True
    except ApiException as e:
        raise KubernetesServiceError(f"pvc check failed: {e}") from e

    try:
        ingresses = networking_v1.list_namespaced_ingress(namespace, label_selector=label_selector)
        if ingresses.items:
            result["ingress_ready"] = all(
                bool(ing.status.load_balancer.ingress) for ing in ingresses.items
            )
        else:
            result["ingress_ready"] = True  # no ingress expected for this tenant
    except ApiException as e:
        raise KubernetesServiceError(f"ingress check failed: {e}") from e

    return result


def is_fully_ready(cluster_context: str, namespace: str, instance_label: str) -> bool:
    health = deployment_health(cluster_context, namespace, instance_label)
    return all(
        [health["namespace_exists"], health["deployment_ready"], health["pods_ready"],
         health["pvc_bound"], health["ingress_ready"]]
    )


def namespace_fully_deleted(cluster_context: str, namespace: str) -> bool:
    return not namespace_exists(cluster_context, namespace)


def delete_namespace(cluster_context: str, namespace: str) -> None:
    """
    Argo CD's CreateNamespace=true sync option creates a tenant's namespace
    as a side effect but never tracks it as a resource owned by the
    Application -- pruning the Application (via the GitOps manifest delete)
    removes every workload inside the namespace but leaves the namespace
    itself around forever. Issue the delete directly so tenant teardown
    actually completes; this is the one intentional exception to this
    module's read-only rule.
    """
    v1 = client.CoreV1Api(_get_api_client(cluster_context))
    try:
        v1.delete_namespace(namespace)
    except ApiException as e:
        if e.status == 404:
            return
        raise KubernetesServiceError(f"namespace delete failed: {e}") from e
