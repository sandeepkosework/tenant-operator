"""TENANT_ID, VIRTUAL_HOST, REDIS_HOST and REDIS_PORT come from the chart, not Vault.

No real Vault or cluster: Vault writes are replaced by a fake that records them.
"""
import pytest

from app.services import vault_service


@pytest.fixture
def vault(monkeypatch):
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


def _run():
    return vault_service.write_initial_qraie_bridge_tenant_secrets("hbss-036", "hbss-036", "hbss-036.qraie.ai")


def test_chart_provided_keys_are_not_written_to_vault(vault):
    _run()
    for path, data in vault.items():
        if path.endswith("/qraie-bot"):
            continue
        leaked = vault_service.CHART_PROVIDED_KEYS & data.keys()
        # the two redis services keep TENANT_ID alone so their path is not empty
        if path.rsplit("/", 1)[1] in ("qraie-redis", "bridge-cp-conductor-redis"):
            assert leaked <= {"TENANT_ID"}, path
        else:
            assert not leaked, f"{path} still has {leaked}"


def test_redis_password_stays_in_vault_and_is_the_same_everywhere(vault):
    _run()
    values = {p: d["REDIS_PASSWORD"] for p, d in vault.items() if "REDIS_PASSWORD" in d}
    assert "k8s/hbss-036/service-common" in values and "k8s/hbss-036/qraie-redis-shared" in values
    assert len(set(values.values())) == 1


def test_returned_mapping_still_has_the_keys_for_the_mongo_mirror(vault):
    written = _run()
    assert written["controlops-server"]["REDIS_HOST"] == "hbss-036-qraie-redis-shared"
    assert written["controlops-server"]["TENANT_ID"] == "hbss-036"
    assert written["controlops-server"]["VIRTUAL_HOST"] == "hbss-036.qraie.ai"


def test_redis_host_matches_the_chart_service_name():
    assert vault_service.tenant_redis_host("hbss-036-45") == "hbss-036-45-qraie-redis-shared"
    # a slug starting with a digit gets the chart's "t-" prefix
    assert vault_service.tenant_redis_host("00042-acme") == "t-00042-acme-qraie-redis-shared"
