"""The QraieBot credential is made once and is the same in Vault and in the seed Job.

No real Vault or cluster: Vault reads/writes are replaced by fakes that record them.
"""
import re
import types

import bcrypt
import pytest

from app.services import bridge_meta_builder_job, vault_service


def test_credential_is_a_password_with_its_matching_bcrypt_hash():
    cred = vault_service.generate_qraie_bot_credential()
    assert cred["password_hash"].startswith("$2b$10$")
    assert bcrypt.checkpw(cred["password"].encode(), cred["password_hash"].encode())
    assert re.fullmatch(r"[A-Za-z0-9_]{24}", cred["password"])


def test_credentials_differ_per_call():
    a, b = vault_service.generate_qraie_bot_credential(), vault_service.generate_qraie_bot_credential()
    assert a["password"] != b["password"] and a["password_hash"] != b["password_hash"]


@pytest.fixture
def vault(monkeypatch):
    """Runs write_initial_qraie_bridge_tenant_secrets against a recording fake."""
    writes = {}
    s = vault_service.settings
    monkeypatch.setattr(s, "vault_enabled", True)
    monkeypatch.setattr(s, "vault_tenant_secret_prefix", "k8s")
    monkeypatch.setattr(s, "mssql_admin_host", None)
    monkeypatch.setattr(s, "mongo_env_config_uri", None)
    monkeypatch.setattr(vault_service, "_write", lambda path, data: writes.__setitem__(path, dict(data)))
    monkeypatch.setattr(vault_service, "read_qraie_bridge_platform_defaults", lambda: {})
    monkeypatch.setattr(vault_service, "_read_tenant_base_secret", lambda service: {})
    return writes


def _run(vault_writes):
    return vault_service.write_initial_qraie_bridge_tenant_secrets("hbss-036", "hbss-036", "hbss-036.qraie.ai")


def test_conductor_gets_the_hash_by_default_and_the_seed_pair_is_the_same_credential(vault):
    written = _run(vault)
    conductor = vault["k8s/hbss-036/bridge-cp-conductor"]
    pair = vault["k8s/hbss-036/qraie-bot"]
    assert conductor["WORKPLACE_DM_PASSWORD"] == pair["password_hash"]
    assert bcrypt.checkpw(pair["password"].encode(), conductor["WORKPLACE_DM_PASSWORD"].encode())
    # the pair is kept out of the per-service mapping that is mirrored to Mongo
    assert "qraie-bot" not in written


def test_conductor_gets_the_plain_password_when_configured(vault, monkeypatch):
    monkeypatch.setattr(vault_service.settings, "workplace_dm_password_as", "plain")
    _run(vault)
    assert vault["k8s/hbss-036/bridge-cp-conductor"]["WORKPLACE_DM_PASSWORD"] == vault["k8s/hbss-036/qraie-bot"]["password"]


def test_every_service_that_has_the_key_gets_the_same_value(vault):
    _run(vault)
    values = {path: d["WORKPLACE_DM_PASSWORD"] for path, d in vault.items() if "WORKPLACE_DM_PASSWORD" in d}
    assert values and len(set(values.values())) == 1


def test_each_tenant_gets_its_own_credential(vault):
    _run(vault)
    first = vault["k8s/hbss-036/qraie-bot"]["password"]
    _run(vault)
    assert vault["k8s/hbss-036/qraie-bot"]["password"] != first


def test_nothing_is_written_when_vault_is_disabled(vault, monkeypatch):
    monkeypatch.setattr(vault_service.settings, "vault_enabled", False)
    _run(vault)
    assert vault == {}


def test_read_back_returns_the_pair_or_none(monkeypatch):
    monkeypatch.setattr(vault_service, "read_qraie_bridge_tenant_secret",
                        lambda slug, svc: {"password": "p", "password_hash": "h"} if svc == "qraie-bot" else None)
    assert vault_service.read_qraie_bot_credential("hbss-036") == {"password": "p", "password_hash": "h"}
    monkeypatch.setattr(vault_service, "read_qraie_bridge_tenant_secret", lambda slug, svc: None)
    assert vault_service.read_qraie_bot_credential("hbss-036") is None
    monkeypatch.setattr(vault_service, "read_qraie_bridge_tenant_secret", lambda slug, svc: {"password": "p"})
    assert vault_service.read_qraie_bot_credential("hbss-036") is None   # half a pair is not used


# --- the seed Job's env -------------------------------------------------------------------

def _job_env(monkeypatch, credential):
    s = bridge_meta_builder_job.settings
    monkeypatch.setattr(s, "mongo_env_config_uri", "mongodb://root:pw@mongo:27017/?authSource=admin")
    monkeypatch.setattr(s, "mssql_admin_host", "sql")
    secrets = {"controlops-server": {"DB_NAME": "hbss-036", "DB_USER": "hbss-036", "DB_PASSWORD": "x"},
               "qraie-api-gateway": {"G_JWT_SECRETKEY": "j", "G_RT_SECRETKEY": "r"}}
    monkeypatch.setattr(bridge_meta_builder_job.vault_service, "read_qraie_bridge_tenant_secret",
                        lambda slug, svc: secrets.get(svc))
    monkeypatch.setattr(bridge_meta_builder_job.vault_service, "read_tenant_common_value", lambda k: "")
    monkeypatch.setattr(bridge_meta_builder_job.vault_service, "read_qraie_bot_credential", lambda slug: credential)
    tenant = types.SimpleNamespace(slug="hbss-036", tenant_name="hbss-036", display_name="HBSS 036",
                                   email="ops@hbss.local", domain="hbss-036.qraie.ai")
    job = bridge_meta_builder_job.build_bridge_meta_builder_job(tenant, "adminpw")
    return {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_job_env_carries_the_same_credential(monkeypatch):
    env = _job_env(monkeypatch, {"password": "plainpw", "password_hash": "$2b$10$hash"})
    assert env["QRAIE_BOT_PASSWORD"] == "plainpw"
    assert env["QRAIE_BOT_PASSWORD_HASH"] == "$2b$10$hash"


def test_job_env_has_no_credential_for_a_tenant_that_predates_it(monkeypatch):
    env = _job_env(monkeypatch, None)
    assert "QRAIE_BOT_PASSWORD" not in env and "QRAIE_BOT_PASSWORD_HASH" not in env
