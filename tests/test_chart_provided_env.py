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


def test_redis_password_is_only_in_service_common(vault):
    written = _run()
    holders = [p for p, d in vault.items() if "REDIS_PASSWORD" in d]
    assert holders == ["k8s/hbss-036/service-common"]
    # the qraie-redis-shared path keeps TENANT_ID so it is not empty
    assert vault["k8s/hbss-036/qraie-redis-shared"] == {"TENANT_ID": "hbss-036"}
    # the mapping mirrored to Mongo still has the same password for every service
    assert len({d["REDIS_PASSWORD"] for d in written.values() if "REDIS_PASSWORD" in d}) == 1


def test_returned_mapping_still_has_the_keys_for_the_mongo_mirror(vault):
    written = _run()
    assert written["controlops-server"]["REDIS_HOST"] == "hbss-036-qraie-redis-shared"
    assert written["controlops-server"]["TENANT_ID"] == "hbss-036"
    assert written["controlops-server"]["VIRTUAL_HOST"] == "hbss-036.qraie.ai"


def test_redis_host_matches_the_chart_service_name():
    assert vault_service.tenant_redis_host("hbss-036-45") == "hbss-036-45-qraie-redis-shared"
    # a slug starting with a digit gets the chart's "t-" prefix
    assert vault_service.tenant_redis_host("00042-acme") == "t-00042-acme-qraie-redis-shared"


def _fake_vault(monkeypatch, store):
    s = vault_service.settings
    monkeypatch.setattr(s, "vault_enabled", True)
    monkeypatch.setattr(s, "vault_tenant_secret_prefix", "k8s")
    monkeypatch.setattr(vault_service, "_read", lambda path: dict(store[path]) if path in store else None)
    monkeypatch.setattr(vault_service, "_write", lambda path, data: store.__setitem__(path, dict(data)))


def test_prune_removes_only_chart_provided_keys_and_reports_differences(monkeypatch):
    store = {
        "k8s/hbss-036-45/bridge": {"TENANT_ID": "hbss-036-45", "VIRTUAL_HOST": "hbss-036.qraie.ai",
                                   "REDIS_HOST": "hbss-036-45-qraie-redis-shared", "REDIS_PORT": "6379",
                                   "REDIS_PASSWORD": "pw", "PORT": "1"},
        "k8s/hbss-036-45/enrollment-api": {"REDIS_URL": "redis://hbss-036-45-qraie-redis:6379", "SML_BASE_URL": "x"},
        "k8s/hbss-036-45/qraie-redis": {"TENANT_ID": "hbss-036"},
    }
    _fake_vault(monkeypatch, store)

    out = vault_service.prune_chart_provided_keys("hbss-036-45", "hbss-036", "hbss-036.qraie.ai", apply=False)
    assert "TENANT_ID" in store["k8s/hbss-036-45/bridge"]  # a dry run writes nothing
    assert out["services"]["bridge"]["status"] == "would_prune"
    # only the slug-valued TENANT_ID differs from what the chart will set
    assert set(out["services"]["bridge"]["differs"]) == {"TENANT_ID"}

    out = vault_service.prune_chart_provided_keys("hbss-036-45", "hbss-036", "hbss-036.qraie.ai", apply=True)
    assert store["k8s/hbss-036-45/bridge"] == {"REDIS_PASSWORD": "pw", "PORT": "1"}
    assert store["k8s/hbss-036-45/enrollment-api"] == {"SML_BASE_URL": "x"}  # REDIS_URL matched the chart's, so not in differs
    # a path that would be left empty is skipped, not written
    assert out["services"]["qraie-redis"]["status"] == "skipped"
    assert store["k8s/hbss-036-45/qraie-redis"] == {"TENANT_ID": "hbss-036"}


def test_prune_removes_redis_password_from_service_paths_only_when_it_matches_service_common(monkeypatch):
    store = {
        "k8s/hbss-036-45/service-common": {"REDIS_PASSWORD": "pw", "ACTIVE_COLOR": "blue"},
        "k8s/hbss-036-45/bridge": {"REDIS_PASSWORD": "pw", "PORT": "1"},
        "k8s/hbss-036-45/acl-server": {"REDIS_PASSWORD": "other", "PORT": "2"},
        "k8s/hbss-036-45/qraie-redis-shared": {"TENANT_ID": "hbss-036", "REDIS_PASSWORD": "pw"},
    }
    _fake_vault(monkeypatch, store)

    out = vault_service.prune_chart_provided_keys("hbss-036-45", "hbss-036", "hbss-036.qraie.ai", apply=True)
    assert store["k8s/hbss-036-45/service-common"] == {"REDIS_PASSWORD": "pw", "ACTIVE_COLOR": "blue"}
    assert store["k8s/hbss-036-45/bridge"] == {"PORT": "1"}
    # a different password is left alone and reported, not removed
    assert store["k8s/hbss-036-45/acl-server"] == {"REDIS_PASSWORD": "other", "PORT": "2"}
    assert out["services"]["acl-server"]["differs"]["REDIS_PASSWORD"]["kept"] is True
    # nothing else would be left, so the path is skipped
    assert out["services"]["qraie-redis-shared"]["status"] == "skipped"


def test_mongodb_uri_is_only_in_service_common(vault):
    written = _run()
    assert [p for p, d in vault.items() if "MONGODB_URI" in d] == ["k8s/hbss-036/service-common"]
    # the services that used it still have it in the mapping mirrored to Mongo
    assert written["erep-server"]["MONGODB_URI"] == written["service-common"]["MONGODB_URI"]
