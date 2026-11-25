"""How a tenant ID turns into the names the platform uses.

A new tenant is named "<name>-bridgestg" on stage and "<name>-bridge" on prod,
e.g. "chetan-bridgestg". The Bridge frontend takes the tenant ID of a login from
the first part of the host name, so what is stored in the databases (SQL username
and tenant_id, Mongo tenantObj.tenantId, ...) is that first part, "chetan". The
full name stays the name of everything else: the host, namespace, Vault path and
the Mongo database.

Tenants created before this have no such suffix ("hbssf"). They keep what they
have: the whole name as the login ID and "<name>-bridge" as the Mongo database.
"""
import re

_NEW_STYLE = re.compile(r"^([a-z0-9]+)-(bridgestg|bridge)$")

# The suffix a tenant created on this environment must carry.
_ENVIRONMENT_SUFFIX = {"stage": "bridgestg", "prod": "bridge"}


def environment_suffix(environment: str) -> str | None:
    """"bridgestg" for stage, "bridge" for prod, None for any other environment."""
    return _ENVIRONMENT_SUFFIX.get((environment or "").lower())


def is_new_style(tenant_name: str) -> bool:
    return _NEW_STYLE.match(tenant_name) is not None


def login_id(tenant_name: str) -> str:
    """The tenant ID the login sends and the databases store: "chetan" for
    "chetan-bridgestg", the whole name for a tenant without the suffix."""
    m = _NEW_STYLE.match(tenant_name)
    return m.group(1) if m else tenant_name


def mongo_db_name(tenant_name: str) -> str:
    """The tenant's Mongo database: the full name for a new-style tenant,
    "<name>-bridge" for an older one."""
    return tenant_name if is_new_style(tenant_name) else f"{tenant_name}-bridge"


def check_tenant_name(tenant_name: str, environment: str) -> str | None:
    """Why this name can't be used on this environment, or None if it can.

    A name with no hyphen is accepted as it always was. A name with a hyphen
    must be "<name>-<suffix>" with the suffix of this environment, because the
    login ID is cut at the first hyphen: any other hyphenated name would log in
    as the wrong tenant.
    """
    if "-" not in tenant_name:
        return None
    m = _NEW_STYLE.match(tenant_name)
    suffix = environment_suffix(environment)
    if m is None:
        shown = f"'<name>-{suffix}'" if suffix else "'<name>-bridgestg' or '<name>-bridge'"
        return (
            f"tenantId '{tenant_name}' is not allowed: use {shown}, with letters and digits only in <name> "
            "(the login ID is the part before the first hyphen)"
        )
    if suffix is not None and m.group(2) != suffix:
        return (
            f"this is the {environment} operator: tenantId must end with '-{suffix}', "
            f"e.g. '{m.group(1)}-{suffix}', not '-{m.group(2)}'"
        )
    return None
