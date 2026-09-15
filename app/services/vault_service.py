"""
Runtime Vault integration for two kinds of variables:

  - COMMON: shared infra config (Redis/Postgres/Mongo hosts+ports, JWT
    signing config, ...) that's the same for every tenant in this
    environment. Lives at secret/{vault_common_secret_path} (one object,
    not per-tenant), managed via PUT/GET /api/v1/vault/common.
  - TENANT-SPECIFIC: the initial per-tenant secrets that bridge-meta-builder
    (the seeding Job) reads via safeVault() at
    secret/tenants/{tenantId}/{redis,database,mongo,jwt} -- see that
    script's `vbase`/`safeVault()` calls for the exact fields expected at
    each path. The seeding Job fails hard ("Missing vault value") if any
    are absent, so this must run before
    meta_builder_job.trigger_meta_builder_job().

Every tenant's secrets are actually COMMON (host/port) + TENANT-SPECIFIC
(generated credential) merged together -- see build_initial_tenant_secrets().

Only writes/reads for real when settings.vault_enabled is True; otherwise
logs/echoes what would happen and falls back to the tenant_* Settings
fields as a stand-in for the common config, same pattern as
crossplane_service.py.

Note on db_username/db_password: these are generated fresh per tenant here.
If your Postgres/Mongo actually connect via a shared service account
instead of a dedicated per-tenant role, replace the generated password
below with that shared secret instead -- this module only controls what
lands in Vault, not what accounts actually exist in Postgres/Mongo.
"""
import logging
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


def _redact(data: dict) -> dict:
    return {k: ("***" if "password" in k or "secret" in k else v) for k, v in data.items()}


# --- Common config (shared across every tenant in this environment) --------

def default_common_config() -> dict:
    """Fallback/seed values, from Settings -- used when Vault is disabled or
    secret/{vault_common_secret_path} hasn't been set yet."""
    return {
        "redis_host": settings.tenant_redis_host,
        "redis_port": settings.tenant_redis_port,
        "postgres_host": settings.tenant_postgres_host,
        "postgres_port": settings.tenant_postgres_port,
        "mongo_host": settings.tenant_mongo_host,
        "mongo_port": settings.tenant_mongo_port,
        "jwt_issuer": "tenant-operator",
        "jwt_algorithm": "HS256",
    }


def read_common_config() -> dict:
    """GET /api/v1/vault/common. Falls back to Settings-derived defaults if
    Vault is disabled or nothing has been pushed to that path yet."""
    if not settings.vault_enabled:
        return default_common_config()

    data = _read(settings.vault_common_secret_path)
    if data is None:
        logger.info(
            "[vault] no common config at %s/%s yet -- returning Settings-derived defaults",
            settings.vault_kv_mount, settings.vault_common_secret_path,
        )
        return default_common_config()
    return data


def write_common_config(data: dict) -> None:
    """PUT /api/v1/vault/common. Overwrites whatever's currently there."""
    logger.info(
        "[vault] writing common config at %s/%s%s: %s",
        settings.vault_kv_mount, settings.vault_common_secret_path,
        " (echoed, vault_enabled=false)" if not settings.vault_enabled else "",
        _redact(data),
    )
    if settings.vault_enabled:
        _write(settings.vault_common_secret_path, data)


# --- Tenant-specific secrets -------------------------------------------------

def build_initial_tenant_secrets(tenant_id: str) -> dict[str, dict]:
    """Split out from write_* so the values can be inspected/tested without a
    real Vault. Merges the common config (hosts/ports/JWT config) with
    freshly generated, tenant-specific credentials."""
    common = read_common_config()
    return {
        "redis": {
            "redis_host": common["redis_host"],
            "password": _generate_secret(),
        },
        "database": {
            "db_host": common["postgres_host"],
            "db_port": str(common["postgres_port"]),
            "db_username": tenant_id,
            "db_password": _generate_secret(),
        },
        "mongo": {
            "db_host": common["mongo_host"],
            "db_port": str(common["mongo_port"]),
            "root_username": f"{tenant_id}_root",
            "root_password": _generate_secret(),
        },
        "jwt": {
            "jwt_issuer": common["jwt_issuer"],
            "jwt_algorithm": common["jwt_algorithm"],
            "jwt_secret": _generate_secret(48),
        },
    }


def write_initial_tenant_secrets(tenant_id: str) -> None:
    """Call once, right before the meta-builder Job is triggered."""
    base = f"{settings.vault_tenant_secret_prefix}/{tenant_id}"
    secrets_by_suffix = build_initial_tenant_secrets(tenant_id)

    logger.info(
        "[vault] tenant=%s writing initial secrets at %s/{%s}%s",
        tenant_id, base, ",".join(secrets_by_suffix.keys()),
        " (echoed, vault_enabled=false)" if not settings.vault_enabled else "",
    )

    for suffix, data in secrets_by_suffix.items():
        if settings.vault_enabled:
            _write(f"{base}/{suffix}", data)
        logger.info("[vault] tenant=%s step: wrote %s/%s -> %s", tenant_id, base, suffix, _redact(data))

    logger.info("[vault] tenant=%s initial secrets ready", tenant_id)


# --- qraie-bridge tenant secrets -------------------------------------------
#
# The generic build_initial_tenant_secrets() above writes ONE shape
# (redis/database/mongo/jwt) that the "workplace" chart's Vault Agent
# annotations read (see poc/chart-workplace/templates/deployment.yaml). The
# "qraie-bridge" chart is a ~30-service conversion with its own, much wider
# Vault schema -- one path per service (secret/tenants/<slug>/<service>,
# see charts/qraie-bridge/templates/vaultstaticsecret.yaml and each
# service's `vault.injectKeys`/`vault.mode` in that chart's values.yaml) --
# writing the generic shape for a qraie-bridge tenant leaves every one of
# its services' actual secrets empty. This mirrors that same shape here so
# app_type="qraie-bridge" tenants get real values instead.
#
# Unlike the generic schema, most of these keys are NOT tenant-specific
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
    "common": ["REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "GLOBALAPIBASEURL", "ACTIVE_COLOR", "START_PORT"],
    # These two have no custom `env:` block in the chart (raw redis/
    # redisgears images) -- but every enabled service still gets an
    # ExternalSecret (templates/externalsecret.yaml loops over ALL of
    # .Values.services) and an implicit TENANT_ID secretKeyRef
    # (templates/deployment.yaml), so they still need an entry here or
    # their ExternalSecret would fail ("Secret does not exist") forever.
    "qraie-redis": ["TENANT_ID"],
    "bridge-cp-conductor-redis": ["TENANT_ID"],
    "acl-server": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    # +NEXT_PUBLIC_SOCKET_URL -- present in docker-compose.yml, missing here.
    "admin-panel": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "NEXT_PUBLIC_VERSION", "NEXT_PUBLIC_SOCKET_URL", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
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
    "mcp-client": ["TENANT_ID", "ACTIVE_COLOR", "CONF_URL", "GLOBALAPIBASEURL", "MCP_SERVER_URL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "TZ", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
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
    # +NODE_ENV/NEXT_PUBLIC_SOCKET_URL/NEXT_PUBLIC_VERSION -- present on the
    # qraie_ui_green variant in docker-compose.yml, missing here.
    "qraie-ui": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "NODE_ENV", "NEXT_PUBLIC_SOCKET_URL", "NEXT_PUBLIC_VERSION"],
    "radicale": ["TENANT_ID", "RADICALE_CONFIG", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PORT"],
    "scheduler-agent": ["TENANT_ID", "ACTIVE_COLOR", "CONDUCTOR_API_KEY", "CONDUCTOR_BASE_URL", "CORS_ORIGINS", "GLOBALAPIBASEURL", "MEETING_API_KEY", "MEETING_API_URL", "MEETING_JOIN_BASE_URL", "PORT", "PRISM_BASE_URL", "PRISM_BRIDGE_ENABLED", "PRISM_POLL_INTERVAL_MS", "RADICALE_AGENT_PASS", "RADICALE_AGENT_USER", "RADICALE_BASE_URL", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SESSION_TTL_MINUTES", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "tranops-backend": ["TENANT_ID", "ACTIVE_COLOR", "AGENTS_API_URL", "API_PORT", "ELEVENLABS_API_KEY", "ELEVENLABS_API_URL", "GLOBALAPIBASEURL", "JWT_EXPIRES_IN", "JWT_SECRET", "MCP_SERVER_URL", "MONGODB_URI", "POLLING_INTERVAL_MS", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SLM_API_URL", "SLM_PASSWORD", "SLM_USERNAME", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "tranops-ui": ["TENANT_ID", "ACTIVE_COLOR", "BASE_URL", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT", "VITE_API_URL", "VITE_WS_URL"],
    "voxflow": ["TENANT_ID", "ACTIVE_COLOR", "BASE_PATH", "GLOBALAPIBASEURL", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "RIDE_API_AUTH_TOKEN", "START_PORT", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "wfm-api-gateway": ["TENANT_ID", "ACTIVE_COLOR", "ALLOWED_ORIGINS", "FABREQ_COMMAND_ENDPOINT_MAP", "GLOBALAPIBASEURL", "LOG_LEVEL", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SERVER_HOST", "SERVER_NAME", "SERVER_PORT", "START_PORT", "TZ", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
    "wfm-microservice": ["TENANT_ID", "ACTIVE_COLOR", "CONFIG_API_URL", "GLOBALAPIBASEURL", "GLOBAL_CONN_POOL_CONFIG", "JWT_SECRETS_MAP", "MICROSERVICE_NAME", "NODE_ENV", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SERVER_HOST", "SERVER_PORT", "START_PORT", "TENANT_IDS", "TENANT_KEY", "TZ"],
    "wfm-ui": ["TENANT_ID", "ACTIVE_COLOR", "GLOBALAPIBASEURL", "NEXT_PUBLIC_SOCKET_URL", "NEXT_PUBLIC_VERSION", "NODE_ENV", "PORT", "REDIS_HOST", "REDIS_PASSWORD", "REDIS_PORT", "SERVER_HOST", "START_PORT", "TZ", "VIRTUAL_DEST", "VIRTUAL_HOST", "VIRTUAL_PATH", "VIRTUAL_PORT"],
}

# Which keys, across ALL services above, get a fresh value generated per
# tenant instead of copied from the shared platform defaults -- exactly the
# credential-shaped fields a real per-tenant secret should be (passwords/
# tokens), never a host/URL/flag/business-config value.
QRAIE_BRIDGE_GENERATED_KEYS = {
    "REDIS_PASSWORD", "DB_PASSWORD", "WORKPLACE_DM_PASSWORD", "RADICALE_AGENT_PASS", "JWT_SECRET",
    # qraie-api-gateway's own JWT signing keys -- confirmed tenant-specific
    # (differ between real tenants, not shared platform config) by the
    # tenant-config reference doc. NOTE: real production currently reuses
    # one JWT_SECRET across all tenants for tranops (see JWT_SECRET above,
    # already generated per-tenant here) -- that's flagged in the same
    # reference doc as a likely bug (a token from one tenant would validate
    # for another), so this deliberately does NOT mirror that shortcut.
    "G_JWT_SECRETKEY", "G_RT_SECRETKEY",
}

# Keys computed deterministically from tenant_slug, never sourced from
# platform defaults -- these identify the tenant itself (TENANT_ID/
# TENANT_KEY/TENANT_IDS) or its own database/login (DB_USER/DB_NAME), and
# MUST be unique per tenant. Unlike QRAIE_BRIDGE_GENERATED_KEYS (a random
# credential), a platform default here would mean every tenant shares the
# same identity/DB_USER/DB_NAME -- e.g. meta_builder_job.py's seeding Job
# would then operate on the SAME database/login for every tenant, silently
# overwriting each other's data and (for DB_USER) each other's password.
QRAIE_BRIDGE_TENANT_DERIVED_KEYS = {"TENANT_ID", "TENANT_KEY", "TENANT_IDS", "DB_USER", "DB_NAME", "DB_SCHEMA"}


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
        result[service] = data if data is not None else {k: "" for k in keys}
    return result


def write_qraie_bridge_platform_defaults(service: str, data: dict) -> None:
    """PUT /api/v1/vault/qraie-bridge-defaults/{service}. Overwrites the
    shared (non-tenant-specific) config for one service -- e.g. the real
    SLM_API_URL/SLM_PASSWORD for tranops-backend, set once per environment,
    not per tenant."""
    if service not in QRAIE_BRIDGE_SERVICE_KEYS:
        raise VaultServiceError(f"unknown qraie-bridge service: {service}")
    logger.info(
        "[vault] writing qraie-bridge platform defaults for service=%s%s: %s",
        service, " (echoed, vault_enabled=false)" if not settings.vault_enabled else "", _redact(data),
    )
    if settings.vault_enabled:
        _write(f"{QRAIE_BRIDGE_PLATFORM_DEFAULTS_PATH}/{service}", data)


def write_initial_qraie_bridge_tenant_secrets(tenant_slug: str, tenant_domain: str) -> dict[str, dict]:
    """Call once, right before the GitOps handoff -- same timing/reasoning
    as write_initial_tenant_secrets() above (the tenant's Vault Agent/VSO
    reads block pod startup until these paths exist). Writes
    secret/tenants/{tenant_slug}/<service> for every qraie-bridge service,
    merging this environment's shared platform defaults with freshly
    generated per-tenant credentials for the keys in
    QRAIE_BRIDGE_GENERATED_KEYS.

    tenant_domain fills VIRTUAL_HOST specifically -- unlike every other
    self-referencing URL key (WORKPLACE_DM_SOCKET_URL, NEXT_PUBLIC_SOCKET_URL,
    VITE_API_URL, ...), VIRTUAL_HOST is always just the bare tenant domain
    with no service-specific path suffix, so it's the one URL-shaped key
    safe to derive automatically rather than requiring a platform-default
    entry someone fills in per tenant. The others still come from
    platform_defaults below -- their path suffix differs per service (and
    the source reference data for this schema showed real inconsistencies
    between environments for a couple of them), so guessing a formula for
    each one risks being confidently wrong. Fill those in per tenant via
    PUT /api/v1/vault/qraie-bridge-defaults/{service} (or a future
    per-tenant override) until that's worth automating too.

    Returns the full service -> resolved-env-dict mapping just written, so
    a caller (mongo_service.write_tenant_env_config()) can mirror the exact
    same data elsewhere without re-deriving or re-reading it from Vault."""
    platform_defaults = read_qraie_bridge_platform_defaults()
    base = f"{settings.vault_tenant_secret_prefix}/{tenant_slug}"
    written: dict[str, dict] = {}

    for service, keys in QRAIE_BRIDGE_SERVICE_KEYS.items():
        platform = platform_defaults.get(service, {})
        data = {}
        for k in keys:
            if k == "VIRTUAL_HOST":
                data[k] = tenant_domain
            elif k in QRAIE_BRIDGE_TENANT_DERIVED_KEYS:
                data[k] = tenant_slug
            elif k in QRAIE_BRIDGE_GENERATED_KEYS:
                data[k] = _generate_secret()
            else:
                data[k] = platform.get(k, "")
        logger.info(
            "[vault] tenant=%s writing qraie-bridge secret %s/%s%s: %s",
            tenant_slug, base, service, " (echoed, vault_enabled=false)" if not settings.vault_enabled else "",
            _redact(data),
        )
        if settings.vault_enabled:
            _write(f"{base}/{service}", data)
        written[service] = data

    logger.info("[vault] tenant=%s qraie-bridge secrets ready (%d services)", tenant_slug, len(QRAIE_BRIDGE_SERVICE_KEYS))
    return written


def read_qraie_bridge_tenant_secret(tenant_slug: str, service: str) -> dict | None:
    """Reads back one service's already-written secret/tenants/{slug}/{service}
    -- used by meta_builder_job.py to pull the tenant's own generated DB_*
    credentials (written by write_initial_qraie_bridge_tenant_secrets()
    before the GitOps handoff) into the seeding Job's env, without needing
    to give that Job its own Vault auth. None if Vault is disabled or the
    path doesn't exist."""
    if not settings.vault_enabled:
        return None
    return _read(f"{settings.vault_tenant_secret_prefix}/{tenant_slug}/{service}")


def read_tenant_secrets(tenant_id: str, app_type: str = "workplace") -> dict:
    """GET /api/v1/tenant/{id}/vault. Reads back what's currently stored for
    this tenant -- the generic redis/database/mongo/jwt suffixes for
    app_type="workplace", or every qraie-bridge service path for
    app_type="qraie-bridge" -- or a note explaining why there's nothing to
    read if Vault is disabled."""
    if not settings.vault_enabled:
        return {"vaultEnabled": False, "note": "VAULT_ENABLED=false -- secrets are only echoed/logged, never actually written"}

    base = f"{settings.vault_tenant_secret_prefix}/{tenant_id}"
    result = {"vaultEnabled": True}
    suffixes = QRAIE_BRIDGE_SERVICE_KEYS.keys() if app_type == "qraie-bridge" else ("redis", "database", "mongo", "jwt")
    for suffix in suffixes:
        data = _read(f"{base}/{suffix}")
        result[suffix] = data  # None if that path doesn't exist yet
    return result
