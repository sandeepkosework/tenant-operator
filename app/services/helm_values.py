from pathlib import Path

import yaml
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
    chart_version: str | None = None,
) -> str:
    """Renders a tenant's values.yaml for the qraie-bridge chart -- the only
    chart tenants are provisioned onto (see app/models/tenant.py's
    `app_type` docstring for the retired "workplace" chart this replaced).

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
        chart_version=chart_version,
    )


# Per-tenant overrides an operator edits by hand in tenants/<slug>.yaml (the chart's
# imageTags / imageRepositories / replicaOverrides / resourceOverrides, see its README).
# They are not in the database, so a re-render has to copy them from the file it replaces.
OVERRIDE_KEYS = ("imageTags", "imageRepositories", "replicaOverrides", "resourceOverrides")

_OVERRIDE_HEADER = (
    "\n# Per-tenant overrides, edited by hand and kept by the operator when it re-renders this file.\n"
    "# Each is a map keyed by service name; see the helm-chart-bridge README.\n"
)


def carry_over_overrides(rendered: str, existing: str | None) -> str:
    """Append the override blocks found in `existing` (the tenant's current file) to `rendered`.

    Raises ValueError when the existing file cannot be read as YAML: failing the update is
    better than silently dropping someone's overrides. Empty blocks are not copied.
    """
    if not existing:
        return rendered
    try:
        data = yaml.safe_load(existing) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"existing tenant file is not valid YAML, refusing to overwrite its overrides: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("existing tenant file is not a YAML mapping, refusing to overwrite it")
    kept = {k: data[k] for k in OVERRIDE_KEYS if data.get(k)}
    if not kept:
        return rendered
    return rendered.rstrip("\n") + "\n" + _OVERRIDE_HEADER + yaml.safe_dump(kept, default_flow_style=False, sort_keys=False)

# ---- per-tenant serviceConfig ---------------------------------------------------------------
# Chart release that introduced `serviceConfig` (image + resources per service). An older chart
# ignores the key, so the operator writes the section only for tenants pinned to this or later.
SERVICE_CONFIG_MIN_VERSION = (0, 1, 2)

_SERVICE_CONFIG_HEADER = (
    "\n# The image and resources each service runs for THIS tenant, taken from the chart release above.\n"
    "# Edit a value here (and commit) to change it for this tenant only; the operator keeps your edits\n"
    "# when it rewrites this file. Anything you remove falls back to the chart's own value.\n"
    "# Put a tag in quotes if it looks like a number (tag: '1.10'), or YAML reads it as 1.1.\n"
)


def _version_tuple(tag: str) -> tuple:
    return tuple(int(part) for part in tag[1:].split("."))


def supports_service_config(chart_version: str | None) -> bool:
    return bool(chart_version) and _version_tuple(chart_version) >= SERVICE_CONFIG_MIN_VERSION


def chart_service_defaults(chart_values_yaml: str, disabled_services: list[str] | None = None) -> dict:
    """{service: {image: {repository, tag}, resources: {...}}} for every service the chart enables and
    this tenant does not disable, as found in the chart's values.yaml text."""
    disabled = set(disabled_services or [])
    out = {}
    for svc in (yaml.safe_load(chart_values_yaml) or {}).get("services") or []:
        name = svc.get("name")
        if not name or svc.get("enabled", True) is False or name in disabled:
            continue
        image = svc.get("image") or {}
        entry = {"image": {"repository": image.get("repository"), "tag": image.get("tag")}}
        if svc.get("resources"):
            entry["resources"] = svc["resources"]
        out[name] = entry
    return out


def _flatten(d: dict, prefix: tuple = ()) -> dict:
    flat = {}
    for key, value in (d or {}).items():
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix + (key,)))
        else:
            flat[prefix + (key,)] = value
    return flat


def _unflatten(flat: dict) -> dict:
    out: dict = {}
    for path, value in flat.items():
        node = out
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    return out


def existing_service_config(existing: str | None) -> dict:
    """The serviceConfig section of a tenant's current file ({} when there is none).
    Raises ValueError when the file cannot be read: failing is better than dropping someone's edits."""
    if not existing:
        return {}
    try:
        data = yaml.safe_load(existing) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"existing tenant file is not valid YAML, refusing to overwrite its serviceConfig: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("existing tenant file is not a YAML mapping, refusing to overwrite it")
    cfg = data.get("serviceConfig") or {}
    if not isinstance(cfg, dict):
        raise ValueError("serviceConfig in the existing tenant file is not a mapping, refusing to overwrite it")
    return cfg


def merge_service_config(new_defaults: dict, existing: dict, old_defaults: dict | None = None) -> dict:
    """The serviceConfig to write: the chart's values for every enabled service, with the tenant's
    own edits kept.

    A value in `existing` is kept unless `old_defaults` (the chart release the tenant is moving away
    from) is given and the value still equals that old default, in which case it follows the new
    default. Values the chart has and the file lacks are added; services that are no longer enabled
    are dropped.
    """
    same = lambda a, b: str(a) == str(b)  # noqa: E731 -- a tag may be written as 7 or '7'
    out = {}
    for name, new in new_defaults.items():
        new_flat = _flatten(new)
        cur_flat = _flatten((existing or {}).get(name) or {})
        old_flat = _flatten((old_defaults or {}).get(name) or {}) if old_defaults is not None else None
        merged = {}
        for path, default in new_flat.items():
            if path not in cur_flat:
                merged[path] = default
            elif old_flat is not None and path in old_flat and same(cur_flat[path], old_flat[path]):
                merged[path] = default            # never edited: follow the new release
            else:
                merged[path] = cur_flat[path]
        for path, value in cur_flat.items():      # values the tenant added that the chart does not have
            if path not in new_flat and not (old_flat is not None and path in old_flat and same(value, old_flat[path])):
                merged[path] = value
        out[name] = _unflatten(merged)
    return out


def append_service_config(rendered: str, service_config: dict) -> str:
    """`rendered` with the serviceConfig section added at the end (unchanged when it is empty)."""
    if not service_config:
        return rendered
    body = yaml.safe_dump({"serviceConfig": service_config}, default_flow_style=False, sort_keys=False, width=200)
    return rendered.rstrip("\n") + "\n" + _SERVICE_CONFIG_HEADER + body
