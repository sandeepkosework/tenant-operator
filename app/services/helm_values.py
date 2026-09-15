from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=("yaml.j2",)),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Every services[].name in charts/qraie-bridge/values.yaml -- kept here,
# not derived from that file at runtime, since this repo and the chart
# aren't guaranteed to live side by side in a real deployment (the chart
# gets copied into the GitOps repo, see NEW_CLUSTER_SETUP.md Phase 3).
# Used only to validate TenantCreateRequest.services keys so a typo'd
# service name fails fast at request time instead of silently no-op'ing
# in the chart's disabledServices check. Keep in sync with that chart's
# values.yaml if services are ever added/renamed/removed there.
QRAIE_BRIDGE_SERVICE_NAMES = frozenset({
    "qraie-redis-shared", "qraie-redis", "bridge-cp-conductor-redis", "bridge-cp-conductor", "bridge",
    "controlops-server", "erep-server", "qraie-api-gateway", "microservice-qraie", "qraie-ui",
    "admin-panel", "wfm-api-gateway", "wfm-ui", "wfm-microservice", "mcp-server", "mcp-client",
    "tranops-ui", "tranops-backend", "iot-broker-data", "iot-broker-config", "iot-broker-broker",
    "iot-broker-admin", "iot-broker-web", "iot-broker-loconav-vision", "enrollment-api",
    "acl-server", "prism-ui", "prism-backend", "prism-scanner", "voxflow", "scheduler-agent",
    "radicale",
})


def compute_disabled_services(services: dict[str, bool], only_listed: bool) -> list[str]:
    """Turns a TenantCreateRequest/TenantUpdateRequest `services` map into
    the flat list that becomes the chart's `disabledServices:` -- see that
    key's comment in charts/qraie-bridge/values.yaml.

    Two modes:
      - only_listed=False (default): opt-OUT. `services` is sparse -- only
        `false` entries do anything; anything not mentioned stays enabled.
        {"voxflow": false} disables just voxflow, everything else unchanged.
      - only_listed=True: opt-IN / allowlist. Every service NOT explicitly
        set to `true` in `services` is disabled -- {"bridge": true,
        "qraie-redis-blue": true} keeps only those two, running nothing
        else, without having to spell out the other ~28 as `false`.
    """
    if only_listed:
        return [name for name in QRAIE_BRIDGE_SERVICE_NAMES if not services.get(name, False)]
    return [name for name, enabled in services.items() if not enabled]


def _size_bucket(users: int) -> dict:
    """Very simple tiering; replace with your real sizing rules."""
    if users <= 25:
        return {"replica_count": 1, "cpu_request": "100m", "memory_request": "256Mi",
                 "cpu_limit": "500m", "memory_limit": "512Mi"}
    if users <= 200:
        return {"replica_count": 2, "cpu_request": "250m", "memory_request": "512Mi",
                 "cpu_limit": "1", "memory_limit": "1Gi"}
    return {"replica_count": 3, "cpu_request": "500m", "memory_request": "1Gi",
            "cpu_limit": "2", "memory_limit": "2Gi"}


def render_values_yaml(
    tenant_name: str,
    tenant_slug: str,
    environment: str,
    application: str,
    version: str,
    namespace: str,
    database_size: str,
    users: int,
    argocd_cluster_server: str,
) -> str:
    template = _env.get_template("values.yaml.j2")
    sizing = _size_bucket(users)
    return template.render(
        tenant_name=tenant_name,
        tenant_slug=tenant_slug,
        environment=environment,
        application=application,
        version=version,
        namespace=namespace,
        database_size=database_size,
        argocd_cluster_server=argocd_cluster_server,
        **sizing,
    )


def render_qraie_bridge_values_yaml(
    tenant_name: str,
    tenant_slug: str,
    environment: str,
    namespace: str,
    domain: str,
    argocd_cluster_server: str,
    disabled_services: list[str] | None = None,
    vault_server: str | None = None,
    ingress_class_name: str | None = None,
) -> str:
    """appType=qraie-bridge counterpart to render_values_yaml() above.

    Deliberately a much smaller set of params -- charts/qraie-bridge's own
    values.yaml already carries every service's image/env/volume defaults;
    the only things a per-tenant override needs to supply are which tenant
    this is and where it's going. See that chart's README "Image
    versioning" section for why per-service tags aren't parameterized here
    yet.

    disabled_services renders as the chart's `disabledServices:` list, NOT
    as a `services:` override -- Helm replaces an entire list wholesale on
    override, so overriding `services:` here would mean repeating the
    chart's whole 32-entry array just to skip a couple. See
    charts/qraie-bridge/values.yaml's own comment on `disabledServices` and
    Tenant.disabled_services' docstring for the full reasoning.
    """
    template = _env.get_template("qraie_bridge_values.yaml.j2")
    return template.render(
        tenant_name=tenant_name,
        tenant_slug=tenant_slug,
        environment=environment,
        namespace=namespace,
        domain=domain,
        argocd_cluster_server=argocd_cluster_server,
        disabled_services=disabled_services or [],
        vault_server=vault_server,
        ingress_class_name=ingress_class_name,
    )
