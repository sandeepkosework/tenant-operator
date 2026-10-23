"""Unit tests for the tenant data purge: the guards and the per-store helpers.

No real Vault, Mongo, SQL Server or cluster is used. Everything below is a fake
or a patched setting, so these run anywhere with the operator's requirements.
"""
import types

import pytest

from app.models.tenant import TenantStatus
from app.services import mongo_service, onboarding_service, purge_service, vault_service


def _tenant(name="hbss-036", slug=None, status=TenantStatus.DELETED, environment="stage"):
    return types.SimpleNamespace(
        id="id-" + name, tenant_name=name, slug=slug or name, status=status, environment=environment,
    )


# --- request guards -------------------------------------------------------------------

def test_purge_refused_when_disabled(monkeypatch):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", False)
    with pytest.raises(purge_service.PurgeNotAllowed) as e:
        purge_service.check_purge_allowed(_tenant(), "hbss-036")
    assert e.value.code == "disabled"


def test_purge_refused_for_environment_not_allowed(monkeypatch):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", True)
    monkeypatch.setattr(purge_service.settings, "purge_allowed_environments", ["stage"])
    with pytest.raises(purge_service.PurgeNotAllowed) as e:
        purge_service.check_purge_allowed(_tenant(environment="prod"), "hbss-036")
    assert e.value.code == "environment"


@pytest.mark.parametrize("confirm", [None, "", "HBSS-036", "hbss-03", "other"])
def test_purge_needs_exact_confirm(monkeypatch, confirm):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", True)
    monkeypatch.setattr(purge_service.settings, "purge_allowed_environments", ["stage"])
    with pytest.raises(purge_service.PurgeNotAllowed) as e:
        purge_service.check_purge_allowed(_tenant(), confirm)
    assert e.value.code == "confirm"


def test_purge_allowed_when_everything_matches(monkeypatch):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", True)
    monkeypatch.setattr(purge_service.settings, "purge_allowed_environments", ["stage"])
    purge_service.check_purge_allowed(_tenant(), "hbss-036")  # no exception


class _FakeQuery:
    def __init__(self, live):
        self._live = live

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._live


class _FakeDb:
    def __init__(self, live=None):
        self._live = live

    def query(self, *_):
        return _FakeQuery(self._live)


def test_only_a_deleted_tenant_can_be_purged():
    with pytest.raises(purge_service.PurgeNotAllowed) as e:
        purge_service.check_purgeable(_FakeDb(), _tenant(status=TenantStatus.RUNNING))
    assert e.value.code == "state"


def test_invalid_tenant_name_is_refused():
    with pytest.raises(purge_service.PurgeNotAllowed) as e:
        purge_service.check_purgeable(_FakeDb(), _tenant(name="Bad Name; DROP"))
    assert e.value.code == "name"


def test_purge_refused_while_a_live_tenant_has_the_same_name():
    live = types.SimpleNamespace(status=TenantStatus.RUNNING)
    with pytest.raises(purge_service.PurgeNotAllowed) as e:
        purge_service.check_purgeable(_FakeDb(live=live), _tenant())
    assert e.value.code == "namesake"


def test_purgeable_deleted_tenant_without_namesake():
    purge_service.check_purgeable(_FakeDb(), _tenant())  # no exception


def test_one_purge_at_a_time():
    tid = "tenant-x"
    assert purge_service.begin(tid) is True
    assert purge_service.begin(tid) is False
    assert purge_service.is_running(tid) is True
    purge_service.end(tid)
    assert purge_service.is_running(tid) is False


# --- step runner ------------------------------------------------------------------------

def test_a_failing_step_does_not_stop_the_others(monkeypatch):
    calls = []

    def ok(name):
        def step(tenant, dry_run):
            calls.append(name)
            return purge_service._result(name, "done", "ok")
        return step

    def boom(tenant, dry_run):
        calls.append("boom")
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(purge_service, "_STEPS", [("a", ok("a")), ("b", boom), ("c", ok("c"))])
    results = purge_service.purge_tenant_data(_tenant())
    assert calls == ["a", "boom", "c"]
    assert [r["status"] for r in results] == ["done", "failed", "done"]
    assert "store unreachable" in results[1]["detail"]


# --- SQL Job ----------------------------------------------------------------------------

def test_sql_answer_parsing():
    assert purge_service._parse_sql_purge_answer("DB_DROPPED=1 LOGIN_DROPPED=0") == (1, 0)
    with pytest.raises(ValueError):
        purge_service._parse_sql_purge_answer("SQL_PURGE_FAILED: login failed")
    with pytest.raises(ValueError):
        purge_service._parse_sql_purge_answer("")
    with pytest.raises(ValueError):
        purge_service._parse_sql_purge_answer("garbage")


def test_sql_job_uses_the_validated_tenant_name_only():
    job = purge_service._build_sql_purge_job("purge-x", "hbss-036")
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["PURGE_NAME"] == "hbss-036"
    assert job["spec"]["backoffLimit"] == 0


# --- Mongo helpers ----------------------------------------------------------------------

class _FakeColl:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def count_documents(self, q):
        return sum(1 for d in self.store.get(self.key, []) if d.get("tenant") == q["tenant"])

    def delete_many(self, q):
        self.store[self.key] = [d for d in self.store.get(self.key, []) if d.get("tenant") != q["tenant"]]


class _FakeMongo:
    """dbs: {database name: [collection names]}; shared: documents of the shared env-config collection."""

    def __init__(self, dbs, shared=None):
        self.dbs, self.dropped, self.shared = dict(dbs), [], shared if shared is not None else {}

    def __getitem__(self, name):
        return _FakeMongoDb(self, name)

    def drop_database(self, name):
        self.dropped.append(name)
        self.dbs.pop(name, None)


class _FakeMongoDb:
    def __init__(self, mongo, name):
        self.mongo, self.name = mongo, name

    def list_collection_names(self):
        return list(self.mongo.dbs.get(self.name, []))

    def __getitem__(self, coll):
        return _FakeColl(self.mongo.shared, f"{self.name}/{coll}")


@pytest.fixture
def mongo_env(monkeypatch):
    s = mongo_service.settings
    monkeypatch.setattr(s, "mongo_env_config_enabled", True)
    monkeypatch.setattr(s, "mongo_env_config_shared_db", None)
    monkeypatch.setattr(s, "qraieai_db_name", "qraieai")
    return s


def _use(monkeypatch, fake):
    monkeypatch.setattr(mongo_service, "_get_client", lambda: fake)
    return fake


def test_drops_the_bridge_database_and_the_mirror(monkeypatch, mongo_env):
    fake = _use(monkeypatch, _FakeMongo({"hbss-036-bridge": ["users", "bridgeMetaInfo"], "hbss-036": ["bridge", "workplace"]}))
    results = mongo_service.purge_tenant_databases("hbss-036", "hbss-036")
    assert sorted(fake.dropped) == ["hbss-036", "hbss-036-bridge"]
    assert [r["action"] for r in results] == ["dropped", "dropped"]


def test_dry_run_drops_nothing(monkeypatch, mongo_env):
    fake = _use(monkeypatch, _FakeMongo({"hbss-036-bridge": ["users"], "hbss-036": ["bridge"]}))
    results = mongo_service.purge_tenant_databases("hbss-036", "hbss-036", dry_run=True)
    assert fake.dropped == []
    assert [r["action"] for r in results] == ["would_drop", "would_drop"]


def test_missing_databases_are_reported_absent(monkeypatch, mongo_env):
    fake = _use(monkeypatch, _FakeMongo({}))
    results = mongo_service.purge_tenant_databases("hbss-036", "hbss-036")
    assert fake.dropped == []
    assert [r["action"] for r in results] == ["absent", "absent"]


def test_protected_databases_are_never_dropped(monkeypatch, mongo_env):
    fake = _use(monkeypatch, _FakeMongo({"admin": ["x"], "qraieai": ["x"], "config": ["x"]}))
    for name in ("admin", "qraieai", "config"):
        r = mongo_service._drop_if_allowed(fake, name, dry_run=False)
        assert r["action"] == "skipped"
    assert fake.dropped == []


def test_invalid_names_are_never_dropped(monkeypatch, mongo_env):
    fake = _use(monkeypatch, _FakeMongo({"UPPER": ["x"], "a/b": ["x"], "": ["x"]}))
    for name in ("UPPER", "a/b", "$cmd", ""):
        assert mongo_service._drop_if_allowed(fake, name, dry_run=False)["action"] == "skipped"
    assert fake.dropped == []


def test_a_database_named_like_the_slug_with_foreign_collections_is_kept(monkeypatch, mongo_env):
    fake = _use(monkeypatch, _FakeMongo({"hbss-036-bridge": [], "hbss-036": ["bridge", "customer_orders"]}))
    results = mongo_service.purge_tenant_databases("hbss-036", "hbss-036")
    mirror = [r for r in results if r["database"] == "hbss-036"][0]
    assert mirror["action"] == "skipped" and "customer_orders" in mirror["reason"]
    assert fake.dropped == []


def test_mirror_is_skipped_when_it_is_disabled(monkeypatch, mongo_env):
    monkeypatch.setattr(mongo_env, "mongo_env_config_enabled", False)
    fake = _use(monkeypatch, _FakeMongo({"hbss-036-bridge": ["users"], "hbss-036": ["whatever"]}))
    results = mongo_service.purge_tenant_databases("hbss-036", "hbss-036")
    assert fake.dropped == ["hbss-036-bridge"]
    assert results[1]["action"] == "skipped"


def test_shared_mirror_removes_only_this_tenants_documents(monkeypatch, mongo_env):
    monkeypatch.setattr(mongo_env, "mongo_env_config_shared_db", "tenant-env-config")
    key = f"tenant-env-config/{mongo_service.SHARED_ENV_CONFIG_COLLECTION}"
    fake = _use(monkeypatch, _FakeMongo({}, shared={key: [{"tenant": "hbss-036"}, {"tenant": "hbss-036"}, {"tenant": "other"}]}))
    results = mongo_service.purge_tenant_databases("hbss-036", "hbss-036")
    assert fake.shared[key] == [{"tenant": "other"}]
    assert "tenant-env-config" not in fake.dropped
    assert results[1]["action"] == "removed" and results[1]["documents"] == 2


# --- Vault helpers ----------------------------------------------------------------------

class _FakeKv:
    def __init__(self, tree):
        self.tree, self.deleted = tree, []

    def list_secrets(self, mount_point, path):
        if path not in self.tree:
            raise type("InvalidPath", (Exception,), {})("nope")
        return {"data": {"keys": self.tree[path]}}

    def delete_metadata_and_all_versions(self, mount_point, path):
        self.deleted.append(path)


def _fake_vault(monkeypatch, tree):
    kv = _FakeKv(tree)
    fake_client = types.SimpleNamespace(secrets=types.SimpleNamespace(kv=types.SimpleNamespace(v2=kv)))
    monkeypatch.setattr(vault_service, "_get_client", lambda: fake_client)
    monkeypatch.setattr(vault_service.settings, "vault_tenant_secret_prefix", "k8s")
    return kv


def test_vault_deletes_every_secret_under_the_tenant_only(monkeypatch):
    kv = _fake_vault(monkeypatch, {"k8s/hbss-036": ["bridge", "erep-server", "nested/"], "k8s/hbss-036/nested": ["inner"]})
    found = vault_service.delete_tenant_secrets("hbss-036")
    assert found == ["k8s/hbss-036/bridge", "k8s/hbss-036/erep-server", "k8s/hbss-036/nested/inner"]
    assert sorted(kv.deleted) == found


def test_vault_dry_run_deletes_nothing(monkeypatch):
    kv = _fake_vault(monkeypatch, {"k8s/hbss-036": ["bridge"]})
    assert vault_service.delete_tenant_secrets("hbss-036", dry_run=True) == ["k8s/hbss-036/bridge"]
    assert kv.deleted == []


def test_vault_missing_tenant_folder_is_not_an_error(monkeypatch):
    kv = _fake_vault(monkeypatch, {})
    assert vault_service.delete_tenant_secrets("hbss-036") == []
    assert kv.deleted == []


@pytest.mark.parametrize("slug", ["", "../other", "a/b", "HBSS", "-x", "x y"])
def test_vault_refuses_bad_slugs(monkeypatch, slug):
    kv = _fake_vault(monkeypatch, {"k8s/hbss-036": ["bridge"]})
    with pytest.raises(vault_service.VaultServiceError):
        vault_service.delete_tenant_secrets(slug)
    assert kv.deleted == []


def test_vault_refuses_an_empty_prefix(monkeypatch):
    _fake_vault(monkeypatch, {})
    monkeypatch.setattr(vault_service.settings, "vault_tenant_secret_prefix", "")
    with pytest.raises(vault_service.VaultServiceError):
        vault_service.delete_tenant_secrets("hbss-036")


# --- onboarding server ------------------------------------------------------------------

def test_onboarding_delete_skipped_without_a_url(monkeypatch):
    monkeypatch.setattr(onboarding_service.settings, "onboarding_server_url", None)
    assert onboarding_service.delete_tenant_record(_tenant()) == "skipped"
