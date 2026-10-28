"""
Runtime Vault integration for this tenant's secrets -- see the
"qraie-bridge tenant secrets" section below for the schema
(secret/k8s/{tenant_slug}/<service>) that bridge-meta-builder (the
seeding Job) and each tenant's own workload read via Vault Agent/VSO.

Only writes/reads for real when settings.vault_enabled is True; otherwise
logs/echoes what would happen, same pattern as crossplane_service.py.

Note on generated credentials (QRAIE_BRIDGE_GENERATED_KEYS): these are
generated fresh per tenant here. If your Postgres/Mongo actually connect
via a shared service account instead of a dedicated per-tenant role,
replace the generated password with that shared secret instead -- this
module only controls what lands in Vault, not what accounts actually exist
in Postgres/Mongo.
"""
import logging
import re
import secrets as pysecrets
import string

from app.config import get_settings

logger = logging.getLogger("tenant-operator.vault_service")
settings = get_settings()

_client = None


class VaultServiceError(Exception):
    pass


def _get_client():
    global _client
    if _client is not None:
        return _client

    import hvac

    if not settings.vault_addr:
        raise VaultServiceError("vault_addr is not configured")

    token = settings.vault_token
    if not token and settings.vault_token_file:
        try:
            token = open(settings.vault_token_file).read().strip()
        except OSError as e:
            raise VaultServiceError(f"could not read vault_token_file: {e}") from e
    if not token:
        raise VaultServiceError("neither vault_token nor vault_token_file is configured")

    client = hvac.Client(url=settings.vault_addr, token=token)
    if not client.is_authenticated():
        raise VaultServiceError("Vault client failed to authenticate -- check vault_token")

    _client = client
    return _client


def _generate_secret(length: int = 24) -> str:
    """A uniform random draw from ascii_letters+digits can land on a string
    with, say, zero digits -- unlikely but real (hit in testing), and
    enough to fail MSSQL's default password complexity policy (needs 3 of
    4 character classes: upper/lower/digit/symbol) for any password this
    generates a DB login from (see meta_builder_job.py). Guarantee at
    least one char from each of upper/lower/digit/symbol, then fill the
    rest randomly and shuffle so the guaranteed chars aren't always in the
    same position."""
    classes = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#%^&*-_="]
    guaranteed = [pysecrets.choice(c) for c in classes]
    alphabet = "".join(classes)
    rest = [pysecrets.choice(alphabet) for _ in range(length - len(guaranteed))]
    chars = guaranteed + rest
    pysecrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _generate_db_password(length: int = 24) -> str:
    """Password for a tenant's SQL Server login. Letters, digits and "_"
    only -- _generate_secret()'s symbols (! @ # % ^ & * - =) get mangled on
    the way into the login: meta_builder_job.py passes the value through
    sqlcmd -v and splices it into CREATE LOGIN ... WITH PASSWORD = '...',
    and some symbol combinations end up creating the login with a different
    password than the one stored in Vault, so the tenant's own Jobs and
    services then fail with "Login failed for user". Still satisfies MSSQL's
    complexity policy (upper + lower + digit + non-alphanumeric "_")."""
    classes = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "_"]
    alphabet = string.ascii_letters + string.digits + "_"
    guaranteed = [pysecrets.choice(c) for c in classes]
    rest = [pysecrets.choice(alphabet) for _ in range(length - len(guaranteed))]
    chars = guaranteed + rest
    pysecrets.SystemRandom().shuffle(chars)
    # Start with a letter so no tool ever has to treat it as a number or flag.
    if not chars[0].isalpha():
        i = next(i for i, c in enumerate(chars) if c.isalpha())
        chars[0], chars[i] = chars[i], chars[0]
    return "".join(chars)


def _write(path: str, data: dict) -> None:
    client = _get_client()
    client.secrets.kv.v2.create_or_update_secret(
        mount_point=settings.vault_kv_mount,
        path=path,
        secret=data,
    )


def _read(path: str) -> dict | None:
    client = _get_client()
    try:
        resp = client.secrets.kv.v2.read_secret_version(mount_point=settings.vault_kv_mount, path=path)
    except Exception as e:  # noqa: BLE001 -- hvac raises its own InvalidPath, not worth importing just for this
        if "InvalidPath" in type(e).__name__:
            return None
        raise VaultServiceError(f"failed to read Vault path {settings.vault_kv_mount}/{path}: {e}") from e
    return resp["data"]["data"]


def _list_children(path: str) -> list[str]:
    """Keys directly under `path` ("name" for a secret, "name/" for a folder),
    or [] if the path does not exist."""
    client = _get_client()
    try:
        resp = client.secrets.kv.v2.list_secrets(mount_point=settings.vault_kv_mount, path=path)
    except Exception as e:  # noqa: BLE001 -- see _read(): hvac's InvalidPath is not worth importing
        if "InvalidPath" in type(e).__name__:
            return []
        raise VaultServiceError(f"failed to list Vault path {settings.vault_kv_mount}/{path}: {e}") from e
    return list(resp["data"]["keys"])


_TENANT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def delete_tenant_secrets(tenant_slug: str, dry_run: bool = False) -> list[str]:
    """Removes every secret (all versions and metadata) under
    secret/<vault_tenant_secret_prefix>/<tenant_slug>/ -- the per-service paths
    write_initial_qraie_bridge_tenant_secrets() created. Returns the paths found
    (deleted, or just listed when dry_run). Never touches anything outside that
    one tenant's folder: the slug is validated and the prefix must be set."""
    if not _TENANT_SLUG_RE.fullmatch(tenant_slug or ""):
        raise VaultServiceError(f"refusing to purge Vault secrets for invalid tenant slug '{tenant_slug}'")
    prefix = (settings.vault_tenant_secret_prefix or "").strip("/")
    if not prefix:
        raise VaultServiceError("vault_tenant_secret_prefix is not set; refusing to purge Vault secrets")

    base = f"{prefix}/{tenant_slug}"
    client = _get_client()
    found: list[str] = []
    pending = [base]
    while pending:
        folder = pending.pop()
        for key in _list_children(folder):
            child = f"{folder}/{key.rstrip('/')}"
            if key.endswith("/"):
                pending.append(child)
                continue
            found.append(child)
            if not dry_run:
                client.secrets.kv.v2.delete_metadata_and_all_versions(
                    mount_point=settings.vault_kv_mount, path=child,
                )
                logger.info("[vault] deleted %s/%s", settings.vault_kv_mount, child)
    return sorted(found)


def _redact(data: dict) -> dict:
    return {k: ("***" if "password" in k or "secret" in k else v) for k, v in data.items()}


# --- qraie-bridge tenant secrets -------------------------------------------
#
# The "qraie-bridge" chart is a ~30-service conversion with its own, wide
# Vault schema -- one path per service (secret/k8s/<slug>/<service>, see
# charts/qraie-bridge/templates/vaultstaticsecret.yaml and each service's
# `vault.injectKeys`/`vault.mode` in that chart's values.yaml).
#
# Most of these keys are NOT tenant-specific
# credentials -- they're shared platform/integration config (SLM API creds,
# ElevenLabs key, Meeting API key, external DB hosts, ...) that's the same
# for every tenant in this environment. Only a handful are actually unique
# per tenant (a Redis/DB/JWT credential per service that has one). Same
# split as common vs. tenant-specific above: PUT /api/v1/vault/qraie-bridge-
# defaults sets the shared half once; write_initial_qraie_bridge_tenant_secrets()
# below only ever generates the per-tenant half, then overlays it onto
# whatever's in that shared config.
QRAIE_BRIDGE_PLATFORM_DEFAULTS_PATH = "qraie-bridge/platform-defaults"

# service name -> every key its Vault path holds. Keeps this in one place,
# in code, instead of only existing as ad-hoc `vault kv put` commands --
# see charts/qraie-bridge/values.yaml's per-service `vault.injectKeys`/
# comments for where each of these was derived from.
QRAIE_BRIDGE_SERVICE_KEYS: dict[str, list[str]] = {
    # Layer 2 of helm-chart-bridge's three-layer secrets (servicecommonSecrets):
    # shared by every service of THIS tenant, read from
    # secret/k8s/<slug>/service-common. Was named "common" before that chart
    # change; the platform-defaults entry of the old name is still honoured
    # (see read_qraie_bridge_platform_defaults).
    # FABREQ_COMMAND_ENDPOINT_MAP and MCP_SERVER_URL are derived per tenant (the
    # in-cluster addresses of wfm-microservice and mcp-server) and live HERE, in the layer every service of the tenant
    # shares, not in wfm-api-gateway's own secret -- an own-secret key would sit
    # above this layer and, if empty, override it. See write_initial_*.
    "service-common": ["REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "MONGODB_URI", "GLOBALAPIBASEURL", "ACTIVE_COLOR", "START_PORT", "FABREQ_COMMAND_ENDPOINT_MAP", "MCP_SERVER_URL", "NEXT_PUBLIC_SOCKET_URL"],
    # These two have no custom `env:` block in the chart (raw redis/
    # redisgears images) -- but every enabled service still gets an
    # ExternalSecret (templates/externalsecret.yaml loops over ALL of
    # .Values.services), so the path must exist or the ExternalSecret would
    # fail ("Secret does not exist") forever. TENANT_ID is kept as the one key
    # so the path is never empty, though the chart sets it in the env itself.
    "qraie-redis": ["TENANT_ID"],
    "bridge-cp-conductor-redis": ["TENANT_ID"],
    "acl-server": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    # NEXT_PUBLIC_SOCKET_URL (same /workplace/api value as qraie-ui) lives in
    # service-common now, derived from the tenant's domain -- not listed here, or
    # this service's own copy would override it.
    "admin-panel": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "NEXT_PUBLIC_VERSION", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "bridge": ["TENANT_ID", "ACTIVE_COLOR", "GEMINI_MODEL", "GLOBALAPIBASEURL", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "bridge-cp-conductor": ["TENANT_ID", "API_PORT", "GEMINI_MODEL", "NOTIF_ENG_BASE_URL", "REDIS_CONTAINER", "REDIS_DISPATCHER_PATH", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SR_BASE_PATH", "SR_NAME", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "WORKPLACE_DM_CLIENT_ID", "WORKPLACE_DM_PASSWORD", "WORKPLACE_DM_SOCKET_URL"],
    # REDIS_PASSWORD/ACTIVE_COLOR/START_PORT were missing here despite being
    # part of the compose's common-env-variables anchor this service also
    # inherits -- found by diffing against the real docker-compose.yml.
    "controlops-server": ["TENANT_ID", "ACTIVE_COLOR", "API_PORT", "DB_HOST", "DB_NAME", "DB_PASSWORD", "DB_USER", "GLOBALAPIBASEURL", "MONGODB_URI", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "enrollment-api": ["TENANT_ID", "IOT_BROKER_URL", "REDIS_URL", "SML_BASE_URL", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "erep-server": ["TENANT_ID", "ACTIVE_COLOR", "BASE_URL", "DOCKER_ENABLED", "GLOBALAPIBASEURL", "LOG_LEVEL", "MONGODB_URI", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "iot-broker-admin": ["TENANT_ID", "BASE_URL", "FRONTEND_URL", "PUBLIC_URL", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "iot-broker-broker": ["TENANT_ID", "CONFIG_SERVICE_URL", "DATA_SERVICE_URL", "FRONTEND_URL", "LOG_LEVEL", "NODE_ENV", "PORT", "SOCKET_IO_PATH", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "WEB_PORTAL_URL"],
    "iot-broker-config": ["TENANT_ID", "MONGODB_URI", "NODE_ENV", "PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "iot-broker-data": ["TENANT_ID", "BROKER_SERVICE_URL", "IOT_VLM_BASE_URL", "LOCONAV_WEBHOOK_ALLOW_INSECURE", "LOCONAV_WEBHOOK_AUTO_CREATE_DEVICE", "LOCONAV_WEBHOOK_SECRET", "LOCONAV_WEBHOOK_SECRETS", "MONGODB_URI", "NODE_ENV", "PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "iot-broker-loconav-vision": ["TENANT_ID", "DATA_SERVICE_URL", "FRONTEND_URL", "NODE_ENV", "PORT", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "iot-broker-web": ["TENANT_ID", "BASE_URL", "FRONTEND_URL", "PUBLIC_URL", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PORT", "VITE_BASE_PATH", "VITE_DATA_SERVICE_URL", "VITE_SOCKET_IO_PATH", "VITE_SOCKET_URL", "VITE_WEB_PORTAL_URL"],
    "mcp-client": ["TENANT_ID", "ACTIVE_COLOR", "CONF_URL", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "TZ", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "mcp-server": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "MCP_SERVER_PORT", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "TZ"],
    # Expanded from a 7-key stub to the real set this service reads --
    # Jira-bot integration (BOT_EMAIL/TOKEN), management-routing config
    # (DevDirector/CRM*/ManagementMember*/MANAGMENT), its own SQL Server
    # login, and connection-pool tuning. See docker-compose.yml's
    # microservice-qraie_green block + the tenant-config reference doc.
    "microservice-qraie": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "apiProtocol", "serverIPA", "BOT_EMAIL", "BOT_TOKEN", "syncInterval", "DevDirector", "CRM1", "CRM2", "ManagementMember1", "ManagementMember2", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_SCHEMA", "DB_PORT", "NODE_ENV", "MANAGMENT", "MAX_CONNECTIONS", "MIN_CONNECTIONS", "alertMemberId", "VIDEO_BASE_URL"],
    "prism-backend": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIDEOS_DIR", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "prism-scanner": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "VITE_API_URL"],
    "prism-ui": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "VITE_API_URL"],
    # Expanded from an 11-key stub -- this is the "Workplace" app's own
    # JWT signing config (G_JWT_SECRETKEY/G_RT_SECRETKEY/G_JWT_EXPIRESIN),
    # its SQL Server login, and third-party integration config
    # (Perplexity). See docker-compose.yml's qraie-api-gateway_green block
    # + the tenant-config reference doc.
    "qraie-api-gateway": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "apiProtocol", "serverIPA", "RESPONSE_TIMEOUT", "PerplexityURL", "PerplexityToken", "G_JWT_SECRETKEY", "G_RT_SECRETKEY", "G_JWT_EXPIRESIN", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_SCHEMA", "DB_PORT", "NODE_ENV", "MICROSERVICE_URL"],
    "qraie-redis-shared": ["TENANT_ID", "REDIS_PASSWORD"],
    # +NODE_ENV/NEXT_PUBLIC_VERSION -- present on the qraie_ui_green variant in
    # docker-compose.yml, missing here. NEXT_PUBLIC_SOCKET_URL is in service-common.
    "qraie-ui": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "NODE_ENV", "NEXT_PUBLIC_VERSION"],
    "radicale": ["TENANT_ID", "RADICALE_CONFIG", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PORT"],
    "scheduler-agent": ["TENANT_ID", "ACTIVE_COLOR", "CONDUCTOR_API_KEY", "CONDUCTOR_BASE_URL", "CORS_ORIGINS", "GLOBALAPIBASEURL", "MEETING_API_KEY", "MEETING_API_URL", "MEETING_JOIN_BASE_URL", "PORT", "PRISM_BASE_URL", "PRISM_BRIDGE_ENABLED", "PRISM_POLL_INTERVAL_MS", "RADICALE_AGENT_PASS", "RADICALE_AGENT_USER", "RADICALE_BASE_URL", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SESSION_TTL_MINUTES", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "tranops-backend": ["TENANT_ID", "ACTIVE_COLOR", "AGENTS_API_URL", "API_PORT", "ELEVENLABS_API_KEY", "ELEVENLABS_API_URL", "GLOBALAPIBASEURL", "JWT_EXPIRES_IN", "JWT_SECRET", "MONGODB_URI", "POLLING_INTERVAL_MS", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SLM_API_URL", "SLM_PASSWORD", "SLM_USERNAME", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "tranops-ui": ["TENANT_ID", "ACTIVE_COLOR", "BASE_URL", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "VITE_API_URL", "VITE_WS_URL"],
    "voxflow": ["TENANT_ID", "ACTIVE_COLOR", "BASE_PATH", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "RIDE_API_AUTH_TOKEN", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "wfm-api-gateway": ["TENANT_ID", "ACTIVE_COLOR", "ALLOWED_ORIGINS", "GLOBALAPIBASEURL", "LOG_LEVEL", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SERVER_HOST", "SERVER_NAME", "SERVER_PORT", "START_PORT", "TZ", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "wfm-microservice": ["TENANT_ID", "ACTIVE_COLOR", "CONFIG_API_URL", "GLOBALAPIBASEURL", "GLOBAL_CONN_POOL_CONFIG", "JWT_SECRETS_MAP", "MICROSERVICE_NAME", "NODE_ENV", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SERVER_HOST", "SERVER_PORT", "START_PORT", "TENANT_IDS", "TENANT_KEY", "TZ"],
    "wfm-ui": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "NEXT_PUBLIC_SOCKET_URL", "NEXT_PUBLIC_VERSION", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SERVER_HOST", "START_PORT", "TZ", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
}

# Which keys, across ALL services above, get a fresh value generated per
# tenant instead of copied from the shared platform defaults -- exactly the
# credential-shaped fields a real per-tenant secret should be (passwords/
# tokens), never a host/URL/flag/business-config value.
QRAIE_BRIDGE_GENERATED_KEYS = {
    "REDIS_PASSWORD", "DB_PASSWORD", "RADICALE_AGENT_PASS", "JWT_SECRET",
    # qraie-api-gateway's own JWT signing keys -- confirmed tenant-specific
    # (differ between real tenants, not shared platform config) by the
    # tenant-config reference doc. NOTE: real production currently reuses
    # one JWT_SECRET across all tenants for tranops (see JWT_SECRET above,
    # already generated per-tenant here) -- that's flagged in the same
    # reference doc as a likely bug (a token from one tenant would validate
    # for another), so this deliberately does NOT mirror that shortcut.
    "G_JWT_SECRETKEY", "G_RT_SECRETKEY",
}

# Keys computed deterministically from the tenant, never sourced from
# platform defaults -- these identify the tenant itself (TENANT_ID/
# TENANT_IDS) and MUST be unique per tenant. Unlike
# QRAIE_BRIDGE_GENERATED_KEYS (a random credential), a platform default
# here would mean every tenant shares the same identity.
#
# TENANT_ID is the exception to "from tenant_slug": it is the BARE tenant name
# (e.g. "hbss-010"), not the slug ("hbss-010-45"). The apps derive their Mongo
# database ("<TENANT_ID>-bridge") and look the tenant up by it, and the
# bridge meta-builder seeds "<tenant_name>-bridge" under tenantObj.tenantId =
# the bare name -- so a slug here made the apps use a second, unseeded database
# ("<slug>-bridge") and miss the seeded data. infra-runner also used the bare name.
# TENANT_IDS (wfm-microservice only) gets the same bare name.
QRAIE_BRIDGE_TENANT_DERIVED_KEYS = {"TENANT_ID", "TENANT_IDS"}

# wfm-microservice's TENANT_KEY is the fixed string "wfm" in the compose files
# (TENANT_IDS is the one that names the tenant), not a per-tenant value.
WFM_TENANT_KEY = "wfm"

# The tenant's own SQL Server database/login (DB_USER/DB_NAME/DB_SCHEMA) is
# named after the bare tenant name (the tenantId from the create request,
# e.g. "hbss"), NOT the sequence-suffixed slug ("hbss-15"). Like the keys
# above these are never sourced from platform defaults -- a platform default
# would make meta_builder_job.py's seeding Job operate on the SAME
# database/login for every tenant. Because the name carries no sequence
# number, two tenants created with the same tenantId share one database and
# login; services/preflight.py therefore refuses to provision a tenant whose
# database/login already exists (tenant deletion does not drop it).
QRAIE_BRIDGE_SQL_NAME_KEYS = {"DB_USER", "DB_NAME", "DB_SCHEMA"}


# wfm-microservice's containerPort in helm-chart-bridge's values.yaml (the Service
# port is the same). Keep in step with the chart.
WFM_MICROSERVICE_PORT = 9001
# mcp-server's containerPort in the chart (its MCP_SERVER_PORT/PORT env, same Service port).
MCP_SERVER_PORT = 10011
# bridge-cp-conductor's containerPort in the chart (the Service port is the same).
BRIDGE_CP_CONDUCTOR_PORT = 8461


def _service_common_derived(tenant_slug: str) -> dict[str, str]:
    """Keys that live ONLY in a tenant's service-common secret, with the value
    derived from the tenant: the in-cluster addresses of two services. They must
    not also appear in any service's own secret, which sits above service-common
    in the pod's env and would override it (an empty copy blanks it; another
    service's own value silently replaces it)."""
    svc = _k8s_service_slug(tenant_slug)
    return {
        "FABREQ_COMMAND_ENDPOINT_MAP": f"http://{svc}-wfm-microservice:{WFM_MICROSERVICE_PORT}",
        "MCP_SERVER_URL": f"http://{svc}-mcp-server:{MCP_SERVER_PORT}/mcp",
    }


def _k8s_service_slug(tenant_slug: str) -> str:
    """The prefix the chart puts on every Kubernetes Service name for a tenant
    (templates/_helpers.tpl "tenant-app.slug"): a Service name can't start with a
    digit, so a slug like "00042-acme" becomes "t-00042-acme"."""
    return f"t-{tenant_slug}" if tenant_slug[:1].isdigit() else tenant_slug


def tenant_redis_host(tenant_slug: str) -> str:
    """In-cluster address of the tenant's own qraie-redis-shared Service. The chart
    sets REDIS_HOST/REDIS_PORT in every service's env (serviceRefEnv) from the same
    name, so it is no longer in Vault; the bridge meta-builder Job derives it here."""
    return f"{_k8s_service_slug(tenant_slug)}-qraie-redis-shared"


# Keys helm-chart-bridge now sets in each service's env itself (commonEnv for
# TENANT_ID and VIRTUAL_HOST, serviceRefEnv for REDIS_HOST/REDIS_PORT and
# enrollment-api's REDIS_URL), and which
# win over the envFrom secrets anyway. They are still resolved into the returned
# per-service mapping (mirrored to Mongo), but not written to Vault.
CHART_PROVIDED_KEYS = frozenset({"TENANT_ID", "VIRTUAL_HOST", "REDIS_HOST", "REDIS_PORT", "REDIS_URL"})

# Keys that live only in the tenant's service-common secret, which every service's
# pod already gets (layer 2). One value per tenant, so there is nothing to gain from
# a copy in each service's own secret -- and a stale copy there would override it.
SERVICE_COMMON_ONLY_KEYS = frozenset({"REDIS_PASSWORD", "MONGODB_URI"})


def read_qraie_bridge_platform_defaults() -> dict[str, dict]:
    """GET /api/v1/vault/qraie-bridge-defaults. One dict per service, same
    shape as QRAIE_BRIDGE_SERVICE_KEYS. Missing/never-configured services
    fall back to all-empty-string (mirrors how qraie-bridge's own
    values.yaml already documents several of these as "blank in the source
    compose file, fill in real ones per tenant") rather than failing --
    same reasoning as default_common_config() above."""
    result: dict[str, dict] = {}
    for service, keys in QRAIE_BRIDGE_SERVICE_KEYS.items():
        data = None
        if settings.vault_enabled:
            data = _read(f"{QRAIE_BRIDGE_PLATFORM_DEFAULTS_PATH}/{service}")
            if data is None and service == "service-common":
                data = _read(f"{QRAIE_BRIDGE_PLATFORM_DEFAULTS_PATH}/common")  # pre-rename name
        result[service] = data if data is not None else {k: "" for k in keys}
    return result


def write_qraie_bridge_platform_defaults(service: str, data: dict) -> None:
    """PUT /api/v1/vault/qraie-bridge-defaults/{service}. Overwrites the
    shared (non-tenant-specific) config for one service -- e.g. the real
    SLM_API_URL/SLM_PASSWORD for tranops-backend, set once per environment,
    not per tenant."""
    if service == "common":  # name used before the three-layer secrets change
        service = "service-common"
    if service not in QRAIE_BRIDGE_SERVICE_KEYS:
        raise VaultServiceError(f"unknown qraie-bridge service: {service}")
    logger.info(
        "[vault] writing qraie-bridge platform defaults for service=%s%s: %s",
        service, " (echoed, vault_enabled=false)" if not settings.vault_enabled else "", _redact(data),
    )
    if settings.vault_enabled:
        _write(f"{QRAIE_BRIDGE_PLATFORM_DEFAULTS_PATH}/{service}", data)


def _read_tenant_base_secret(service: str) -> dict:
    """The operator-maintained base secret for `service`
    (secret/{vault_tenant_base_prefix}/{service}), or {} if there isn't one --
    most services won't have one, so a missing path is not worth a warning."""
    if not settings.vault_enabled:
        return {}
    data = _read(f"{settings.vault_tenant_base_prefix}/{service}")
    if not data:
        logger.debug("[vault] no base secret at %s/%s/%s", settings.vault_kv_mount, settings.vault_tenant_base_prefix, service)
    return data or {}


def _tenant_mongodb_uri(tenant_name: str) -> str:
    """This tenant's own MongoDB connection string -- one database per
    tenant, shared by every service in that tenant, not one database per
    service. Built from mongo_env_config_uri (the same shared instance/root
    credentials mongo_service.py's env mirror already uses).

    Database name is "{tenant_name}-bridge" -- tenant_name (the bare,
    human-chosen name, e.g. "hbss"), NOT tenant_slug (the sequence-suffixed
    "hbss-15" used for namespace/Vault-path/git-filename collision
    avoidance elsewhere). This matches the legacy bridge-meta-builder
    system's own convention exactly (its Mongo DB name and public hostname
    were both built from the bare tenant name, never a sequence number),
    which bridge_meta_builder_job.py's seeded collections (bridgeMetaInfo,
    auth, users, wfm_*, controlops_actors, ...) and this MONGODB_URI must
    agree with -- otherwise controlops-server and the meta-builder's own
    seeded data would end up looking at two different databases.

    Returns "" if mongo_env_config_uri isn't configured (mirrors every
    other optional-config fallback in this file)."""
    if not settings.mongo_env_config_uri:
        return ""
    base, sep, query = settings.mongo_env_config_uri.partition("?")
    uri = f"{base.rstrip('/')}/{tenant_name}-bridge"
    return f"{uri}{sep}{query}" if sep else uri


QRAIE_BOT_SECRET_PATH = "qraie-bot"


def generate_qraie_bot_credential() -> dict[str, str]:
    """The tenant's QraieBot credential, made once: a plain password and its bcrypt
    hash. The seed stores the hash (SQL system_api_accesstokens, Mongo
    workplace.authTokenPassword) and uses the plain password in the actor
    templates; bridge-cp-conductor logs in with WORKPLACE_DM_PASSWORD. They used to
    be generated independently (a random Vault value and a separate random seed
    value), so the conductor's login could never match. Letters, digits and "_"
    only, so the value survives every place it is spliced into."""
    import bcrypt

    password = _generate_db_password()
    return {
        "password": password,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode(),
    }


def read_qraie_bot_credential(tenant_slug: str) -> dict | None:
    """The credential write_initial_qraie_bridge_tenant_secrets() stored, for the
    seed Job's env. None if Vault is disabled, or the tenant predates this."""
    data = read_qraie_bridge_tenant_secret(tenant_slug, QRAIE_BOT_SECRET_PATH)
    if data and data.get("password") and data.get("password_hash"):
        return {"password": data["password"], "password_hash": data["password_hash"]}
    return None


def write_initial_qraie_bridge_tenant_secrets(tenant_slug: str, tenant_name: str, tenant_domain: str) -> dict[str, dict]:
    """Call once, right before the GitOps handoff -- same timing/reasoning
    as write_initial_tenant_secrets() above (the tenant's Vault Agent/VSO
    reads block pod startup until these paths exist). Writes
    secret/k8s/{tenant_slug}/<service> for every qraie-bridge service,
    merging this environment's shared platform defaults with freshly
    generated per-tenant credentials for the keys in
    QRAIE_BRIDGE_GENERATED_KEYS.

    tenant_domain fills VIRTUAL_HOST specifically -- unlike every other
    self-referencing URL key (WORKPLACE_DM_SOCKET_URL, NEXT_PUBLIC_SOCKET_URL,
    VITE_API_URL, ...), VIRTUAL_HOST is always just the bare tenant domain
    with no service-specific path suffix, so it's the one URL-shaped key
    safe to derive automatically rather than requiring a platform-default
    entry someone fills in per tenant. MONGODB_URI is similarly derived
    (see _tenant_mongodb_uri) rather than a platform default -- the source
    reference data showed some services sharing one fixed database name
    across every tenant, which defeats tenant isolation; every service that
    needs Mongo gets this same tenant's own database instead. Every other
    self-referencing URL key still comes from platform_defaults below --
    their path suffix differs per service (and the source reference data
    for this schema showed real inconsistencies between environments for a
    couple of them), so guessing a formula for each one risks being
    confidently wrong. Fill those in per tenant via
    PUT /api/v1/vault/qraie-bridge-defaults/{service} (or a future
    per-tenant override) until that's worth automating too.

    Returns the full service -> resolved-env-dict mapping just written, so
    a caller (mongo_service.write_tenant_env_config()) can mirror the exact
    same data elsewhere without re-deriving or re-reading it from Vault."""
    platform_defaults = read_qraie_bridge_platform_defaults()
    base = f"{settings.vault_tenant_secret_prefix}/{tenant_slug}"
    written: dict[str, dict] = {}
    mongodb_uri = _tenant_mongodb_uri(tenant_name)

    # This tenant's own qraie-redis-shared instance -- every service that
    # talks to Redis (including qraie-redis-shared itself, via its
    # `--requirepass $(REDIS_PASSWORD)` command) MUST agree on the exact
    # same host/port/password, or the redis-server's own auth rejects every
    # client. REDIS_HOST/PORT used to fall through to platform_defaults
    # (a single literal meant to be the same for every tenant) and
    # REDIS_PASSWORD was generated independently per service -- both wrong,
    # since the chart deploys a separate qraie-redis-shared pod per tenant,
    # not one shared platform-wide instance. Host mirrors the chart's own
    # "tenant-app.slug" helper (helm-chart-bridge/templates/_helpers.tpl):
    # Kubernetes Service names can't start with a digit, so a tenant_slug
    # like "00001-verify02" gets a "t-" prefix there, and this must match.
    redis_dns_slug = _k8s_service_slug(tenant_slug)
    redis_host = tenant_redis_host(tenant_slug)
    # Where the wfm-api-gateway reaches wfm-microservice: the chart's per-tenant
    # Service "<slug>-wfm-microservice" on the container port. The old compose file
    # had the container name "http://wfm-microservice:9001", which doesn't resolve
    # on Kubernetes.
    service_common_derived = _service_common_derived(tenant_slug)
    tenant_redis_port = "6379"
    tenant_redis_password = _generate_secret()
    # One SQL login per tenant (created by the seeding Job from this value), so every
    # service that connects to SQL Server must get the SAME password, not its own.
    tenant_db_password = _generate_db_password()
    # One QraieBot credential per tenant, shared by Vault (WORKPLACE_DM_PASSWORD, below)
    # and the seed Job (read back from <base>/qraie-bot).
    qraie_bot = generate_qraie_bot_credential()
    dm_password = qraie_bot["password_hash"] if settings.workplace_dm_password_as == "hash" else qraie_bot["password"]

    for service, keys in QRAIE_BRIDGE_SERVICE_KEYS.items():
        platform = platform_defaults.get(service, {})
        base_secret = _read_tenant_base_secret(service)
        data = {}
        for k in keys:
            if k == "VIRTUAL_HOST":
                data[k] = tenant_domain
            elif k == "CONDUCTOR_BASE_URL":
                # scheduler-agent -> this tenant's own conductor, via its
                # Kubernetes Service (compose: http://bridge-cp-conductor:8461).
                data[k] = f"http://{redis_dns_slug}-bridge-cp-conductor:{BRIDGE_CP_CONDUCTOR_PORT}"
            elif service == "service-common" and k == "NEXT_PUBLIC_SOCKET_URL":
                # The workplace UIs' (qraie-ui, admin-panel) socket endpoint on the
                # tenant's own domain. wfm-ui keeps its own, different path
                # (/organization/wfm/api/) in its own secret, which overrides this.
                data[k] = f"https://{tenant_domain}/workplace/api"
            elif service == "tranops-ui" and k == "VITE_API_URL":
                # tranops-ui's own browser-side API/socket endpoints on the
                # tenant's domain (compose: https://<host>/tranops/api and https://<host>).
                data[k] = f"https://{tenant_domain}/tranops/api"
            elif service == "tranops-ui" and k == "VITE_WS_URL":
                data[k] = f"https://{tenant_domain}"
            elif k == "WORKPLACE_DM_SOCKET_URL":
                # The tenant's own public URL (compose: https://<VIRTUAL_HOST>),
                # so it can't come from the shared defaults/base secret.
                data[k] = f"https://{tenant_domain}"
            elif k == "MONGODB_URI":
                data[k] = mongodb_uri
            elif k in service_common_derived:
                data[k] = service_common_derived[k]
            elif k == "REDIS_HOST":
                data[k] = redis_host
            elif k == "REDIS_PORT":
                data[k] = tenant_redis_port
            elif k == "REDIS_PASSWORD":
                data[k] = tenant_redis_password
            elif k == "DB_PASSWORD":
                data[k] = tenant_db_password
            elif k == "WORKPLACE_DM_PASSWORD":
                data[k] = dm_password
            elif k == "DB_HOST" and settings.mssql_admin_host:
                data[k] = settings.mssql_admin_host
            elif k == "DB_PORT" and settings.mssql_admin_host:
                data[k] = str(settings.mssql_admin_port)
            elif k in QRAIE_BRIDGE_SQL_NAME_KEYS:
                data[k] = tenant_name
            elif k in ("TENANT_ID", "TENANT_IDS"):
                data[k] = tenant_name
            elif k == "TENANT_KEY":
                data[k] = WFM_TENANT_KEY
            elif k in QRAIE_BRIDGE_TENANT_DERIVED_KEYS:
                data[k] = tenant_slug
            elif k in QRAIE_BRIDGE_GENERATED_KEYS:
                data[k] = _generate_secret()
            else:
                data[k] = base_secret[k] if k in base_secret else platform.get(k, "")
        if base_secret:
            # Keys the base secret has that this service's schema doesn't list
            # are copied as-is; keys the tenant-derived branches above already
            # decided (TENANT_ID, REDIS_*, DB_PASSWORD, ...) keep their value.
            data.update({k: v for k, v in base_secret.items() if k not in data})
            kept = sorted(k for k in base_secret if data.get(k) != base_secret[k])
            logger.info(
                "[vault] tenant=%s copied base secret %s/%s (%d keys)%s",
                tenant_slug, settings.vault_tenant_base_prefix, service, len(base_secret),
                f"; tenant-specific value kept for: {', '.join(kept)}" if kept else "",
            )
        # Don't write keys that have no value. The pod's env is built from three
        # secrets (tenant-common < service-common < this service's own) and a later
        # one wins on a key it defines -- even with an empty string -- so an empty
        # GLOBALAPIBASEURL here would silently override the real value an
        # operator set once in secret/k8s/tenant-common. Left out, the lower layer
        # supplies it. (If NOTHING has a value the keys are written anyway: the
        # path has to exist or the pod's ExternalSecret can't sync.)
        # Keys the chart sets in the pod's env itself are left out too. If that leaves
        # nothing (qraie-redis, bridge-cp-conductor-redis only had TENANT_ID), the keys
        # are written anyway, for the same reason as above.
        # SERVICE_COMMON_ONLY_KEYS are written to service-common only.
        not_here = CHART_PROVIDED_KEYS | (SERVICE_COMMON_ONLY_KEYS if service != "service-common" else frozenset())
        payload = {k: v for k, v in data.items() if v != "" and k not in not_here} or {
            k: v for k, v in data.items() if k not in not_here - CHART_PROVIDED_KEYS
        }
        skipped = len(data) - len(payload)
        logger.info(
            "[vault] tenant=%s writing qraie-bridge secret %s/%s%s: %s%s",
            tenant_slug, base, service, " (echoed, vault_enabled=false)" if not settings.vault_enabled else "",
            _redact(payload),
            f" (+{skipped} empty key(s) not written, so tenant-common/service-common can supply them)" if skipped else "",
        )
        if settings.vault_enabled:
            _write(f"{base}/{service}", payload)
        written[service] = data

    # The plain password and its hash, for the seed Job (never part of `written`, which
    # is mirrored elsewhere). Under the tenant's own folder, so a data purge removes it.
    if settings.vault_enabled:
        _write(f"{base}/{QRAIE_BOT_SECRET_PATH}", qraie_bot)
    logger.info("[vault] tenant=%s qraie-bridge secrets ready (%d services; QraieBot credential written as %s)",
                tenant_slug, len(QRAIE_BRIDGE_SERVICE_KEYS), settings.workplace_dm_password_as)
    return written


def read_qraie_bridge_tenant_secret(tenant_slug: str, service: str) -> dict | None:
    """Reads back one service's already-written secret/k8s/{slug}/{service}
    -- used by meta_builder_job.py to pull the tenant's own generated DB_*
    credentials (written by write_initial_qraie_bridge_tenant_secrets()
    before the GitOps handoff) into the seeding Job's env, without needing
    to give that Job its own Vault auth. None if Vault is disabled or the
    path doesn't exist."""
    if not settings.vault_enabled:
        return None
    return _read(f"{settings.vault_tenant_secret_prefix}/{tenant_slug}/{service}")


# The org-wide layer of helm-chart-bridge's three-layer secrets
# (secret/<prefix>/tenant-common) -- maintained by hand, never written by
# this operator. It is read here only to copy a value the chart delivers to
# the pods (e.g. PerplexityToken) into data this operator seeds elsewhere.
TENANT_COMMON_PATH = "tenant-common"


def read_tenant_common_value(key: str) -> str:
    """One value from secret/<prefix>/tenant-common, or "" if it can't be
    had. Never raises: a missing value must not abort tenant creation, so
    every reason it's missing is logged as an ERROR naming the exact Vault
    path and key to fix (Vault being disabled is only a WARNING -- that's a
    deliberate dev setting, not a missing secret)."""
    path = f"{settings.vault_kv_mount}/{settings.vault_tenant_secret_prefix}/{TENANT_COMMON_PATH}"
    if not settings.vault_enabled:
        logger.warning("[vault] %s not read from %s: VAULT_ENABLED=false", key, path)
        return ""
    try:
        data = _read(f"{settings.vault_tenant_secret_prefix}/{TENANT_COMMON_PATH}")
    except VaultServiceError as e:
        logger.error("[vault] could not read %s for key '%s': %s", path, key, e)
        return ""
    if data is None:
        logger.error("[vault] %s does not exist -- create it and add key '%s'", path, key)
        return ""
    value = data.get(key)
    if not value:
        logger.error(
            "[vault] key '%s' is %s in %s -- add it (it is also what the tenant pods receive from this path)",
            key, "empty" if key in data else "missing", path,
        )
        return ""
    return value


def prune_empty_tenant_secrets(tenant_slug: str, apply: bool = False) -> dict:
    """Removes keys whose value is an empty string from every one of a tenant's
    Vault paths (secret/<prefix>/<slug>/<service>), so values set once in
    secret/k8s/tenant-common or service-common are no longer overridden by an
    empty key in a service's own secret (older tenants were created with those
    -- see write_initial_qraie_bridge_tenant_secrets). Dry run unless apply=True.

    A path whose every key is empty is left alone (it must keep a key or the
    pod's ExternalSecret can't sync). Writing a path replaces it with a new KV v2
    version holding only the non-empty keys."""
    if not settings.vault_enabled:
        raise VaultServiceError("VAULT_ENABLED=false -- there is nothing in Vault to prune")
    base = f"{settings.vault_tenant_secret_prefix}/{tenant_slug}"
    services: dict[str, dict] = {}
    total = 0
    for service in QRAIE_BRIDGE_SERVICE_KEYS:
        path = f"{base}/{service}"
        data = _read(path)
        if data is None:
            services[service] = {"status": "missing", "removed": []}
            continue
        kept = {k: v for k, v in data.items() if v not in ("", None)}
        removed = sorted(set(data) - set(kept))
        if not removed:
            services[service] = {"status": "ok", "removed": []}
        elif not kept:
            services[service] = {"status": "skipped", "removed": [], "note": "every key is empty; the path must keep at least one key"}
        else:
            if apply:
                _write(path, kept)
            services[service] = {"status": "pruned" if apply else "would_prune", "removed": removed}
            total += len(removed)
    logger.info("[vault] tenant=%s prune-empty %s: %d key(s)", tenant_slug, "APPLIED" if apply else "dry run", total)
    return {"tenant": tenant_slug, "applied": apply, "totalKeysRemoved" if apply else "totalKeysToRemove": total, "services": services}


def prune_chart_provided_keys(tenant_slug: str, tenant_name: str, tenant_domain: str, apply: bool = False) -> dict:
    """For a tenant created before the chart set them in the pods' env: removes
    CHART_PROVIDED_KEYS (TENANT_ID, VIRTUAL_HOST, REDIS_HOST, REDIS_PORT, REDIS_URL) from every
    one of the tenant's Vault paths, and SERVICE_COMMON_ONLY_KEYS (REDIS_PASSWORD, MONGODB_URI)
    from every path except service-common. Dry run unless apply=True. A path left with no keys
    is skipped (it must keep one, or the pod's ExternalSecret can't sync), e.g. qraie-redis.

    A SERVICE_COMMON_ONLY key found in service paths but missing from service-common is first
    added there, if every service path that has it agrees on the value (reported under
    "serviceCommon"). If they disagree nothing is added and the copies stay. A copy that differs
    from service-common's own value is kept and reported, since that service uses it today.

    The chart's value wins over Vault, so removing a chart-provided key changes nothing once the
    chart is deployed -- unless Vault held a DIFFERENT value, which the pods then stop seeing.
    Those are reported under "differs" so it can be checked first; an older tenant's TENANT_ID
    may still be the slug (see QRAIE_BRIDGE_TENANT_DERIVED_KEYS), where the chart's
    tenant.name now applies."""
    if not settings.vault_enabled:
        raise VaultServiceError("VAULT_ENABLED=false -- there is nothing in Vault to change")
    expected = {
        "TENANT_ID": tenant_name,
        "VIRTUAL_HOST": tenant_domain,
        "REDIS_HOST": tenant_redis_host(tenant_slug),
        "REDIS_PORT": "6379",
        "REDIS_URL": f"redis://{_k8s_service_slug(tenant_slug)}-qraie-redis:6379",
    }
    base = f"{settings.vault_tenant_secret_prefix}/{tenant_slug}"
    current = {service: _read(f"{base}/{service}") for service in QRAIE_BRIDGE_SERVICE_KEYS}
    common = dict(current.get("service-common") or {})

    # SERVICE_COMMON_ONLY keys the service paths have but service-common lacks.
    common_report: dict[str, dict] = {}
    for k in sorted(SERVICE_COMMON_ONLY_KEYS):
        if k in common:
            continue
        found = {svc: d[k] for svc, d in current.items() if svc != "service-common" and d and k in d}
        if not found:
            continue
        values = set(found.values())
        if len(values) == 1:
            common[k] = next(iter(values))
            common_report[k] = {"status": "added" if apply else "would_add", "from": sorted(found)}
        else:
            common_report[k] = {"status": "conflict", "from": sorted(found),
                                "note": "the service paths disagree; nothing added, copies kept"}
    if apply and any(r["status"] == "added" for r in common_report.values()):
        _write(f"{base}/service-common", common)

    services: dict[str, dict] = {}
    total = 0
    for service, data in current.items():
        if data is None:
            services[service] = {"status": "missing", "removed": []}
            continue
        removable = set(CHART_PROVIDED_KEYS)
        differs = {}
        if service != "service-common":
            for k in SERVICE_COMMON_ONLY_KEYS & data.keys():
                if k not in common:
                    continue
                if data[k] == common[k]:
                    removable.add(k)
                else:
                    differs[k] = {"vault": "<redacted>", "service-common": "<redacted>", "kept": True}
        removed = sorted(removable & data.keys())
        differs.update({k: {"vault": data[k], "chart": expected[k]} for k in removed if k in expected and data[k] != expected[k]})
        kept = {k: v for k, v in data.items() if k not in removed}
        if not removed:
            services[service] = {"status": "ok", "removed": [], **({"differs": differs} if differs else {})}
        elif not kept:
            services[service] = {"status": "skipped", "removed": [], "differs": differs,
                                 "note": "every key would be removed; the path must keep at least one key"}
        else:
            if apply:
                _write(f"{base}/{service}", kept)
            services[service] = {"status": "pruned" if apply else "would_prune", "removed": removed, "differs": differs}
            total += len(removed)
    logger.info("[vault] tenant=%s prune-chart-provided-keys %s: %d key(s)", tenant_slug, "APPLIED" if apply else "dry run", total)
    return {"tenant": tenant_slug, "applied": apply, "serviceCommon": common_report,
            "totalKeysRemoved" if apply else "totalKeysToRemove": total, "services": services}


def sync_service_common_derived_keys(tenant_slug: str, apply: bool = False) -> dict:
    """For a tenant created before FABREQ_COMMAND_ENDPOINT_MAP and MCP_SERVER_URL
    moved to service-common: sets both in secret/<prefix>/<slug>/service-common (to
    the derived in-cluster addresses) and REMOVES them from every other path,
    whatever their value, so nothing overrides the shared one. Dry run unless
    apply=True. A path left with no keys at all is skipped (it must keep one).
    The removed values are reported (they are URLs, not secrets) so you can see
    what each service had -- e.g. tranops-backend's own MCP_SERVER_URL, which
    it will no longer have."""
    if not settings.vault_enabled:
        raise VaultServiceError("VAULT_ENABLED=false -- there is nothing in Vault to change")
    derived = _service_common_derived(tenant_slug)
    base = f"{settings.vault_tenant_secret_prefix}/{tenant_slug}"
    services: dict[str, dict] = {}
    for service in QRAIE_BRIDGE_SERVICE_KEYS:
        path = f"{base}/{service}"
        data = _read(path)
        if data is None:
            services[service] = {"status": "missing"}
            continue
        if service == "service-common":
            changes = {k: v for k, v in derived.items() if data.get(k) != v}
            if not changes:
                services[service] = {"status": "ok", "set": {}}
                continue
            if apply:
                _write(path, {**data, **changes})
            services[service] = {"status": "updated" if apply else "would_update", "set": changes}
            continue
        removed = {k: data[k] for k in derived if k in data}
        if not removed:
            services[service] = {"status": "ok", "removed": {}}
            continue
        kept = {k: v for k, v in data.items() if k not in derived}
        if not kept:
            services[service] = {"status": "skipped", "removed": {}, "note": "removing the keys would leave the path empty"}
            continue
        if apply:
            _write(path, kept)
        services[service] = {"status": "removed" if apply else "would_remove", "removed": removed}
    logger.info("[vault] tenant=%s sync-shared-keys %s", tenant_slug, "APPLIED" if apply else "dry run")
    return {"tenant": tenant_slug, "applied": apply, "derived": derived, "services": services}


def read_tenant_secrets(tenant_id: str) -> dict:
    """GET /api/v1/tenant/{id}/vault. Reads back what's currently stored for
    this tenant -- every qraie-bridge service path -- or a note explaining
    why there's nothing to read if Vault is disabled."""
    if not settings.vault_enabled:
        return {"vaultEnabled": False, "note": "VAULT_ENABLED=false -- secrets are only echoed/logged, never actually written"}

    base = f"{settings.vault_tenant_secret_prefix}/{tenant_id}"
    result = {"vaultEnabled": True}
    for suffix in QRAIE_BRIDGE_SERVICE_KEYS.keys():
        data = _read(f"{base}/{suffix}")
        result[suffix] = data  # None if that path doesn't exist yet
    return result
