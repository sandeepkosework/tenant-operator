"""
Loads this operator's own config from Vault into the process environment,
BEFORE app.config.Settings is ever constructed. Real env vars and .env
always win over Vault for any key already set -- Vault only fills gaps.
This keeps pydantic-settings as the single source of truth everywhere else
in the codebase; Vault is just another way to populate it.

Deliberately has NO dependency on app.config -- that would be circular,
since this must run before Settings exists. Reads VAULT_* directly from
os.environ instead.

No-ops entirely if VAULT_ADDR isn't set, so this is a pure no-op for
local/no-cluster testing (see poc/.env.nocluster.example) and for any
deployment that just uses env vars/.env/ConfigMaps/Secrets as-is.
"""
import logging
import os

logger = logging.getLogger("tenant-operator.vault_bootstrap")


def load_env_from_vault() -> None:
    vault_addr = os.environ.get("VAULT_ADDR")
    if not vault_addr:
        return

    try:
        import hvac
    except ImportError:
        logger.warning("VAULT_ADDR is set but the 'hvac' package isn't installed -- skipping Vault config load")
        return

    token = os.environ.get("VAULT_TOKEN")
    token_file = os.environ.get("VAULT_TOKEN_FILE")  # e.g. written by a Vault Agent sidecar
    if not token and token_file and os.path.exists(token_file):
        token = open(token_file).read().strip()

    if not token:
        logger.warning("VAULT_ADDR is set but no VAULT_TOKEN/VAULT_TOKEN_FILE was found -- skipping Vault config load")
        return

    mount = os.environ.get("VAULT_KV_MOUNT", "secret")
    path = os.environ.get("VAULT_CONFIG_PATH", "tenant-operator/config")

    try:
        client = hvac.Client(url=vault_addr, token=token)
        resp = client.secrets.kv.v2.read_secret_version(mount_point=mount, path=path)
        data = resp["data"]["data"]
    except Exception as e:  # noqa: BLE001 -- Vault being briefly unreachable shouldn't crash startup
        logger.error(
            "failed to load config from Vault at %s/%s: %s -- falling back to env vars/.env only",
            mount, path, e,
        )
        return

    applied = 0
    for key, value in data.items():
        env_key = key.upper()
        if env_key not in os.environ:
            os.environ[env_key] = str(value)
            applied += 1
    logger.info("loaded %d config value(s) from Vault at %s/%s", applied, mount, path)
