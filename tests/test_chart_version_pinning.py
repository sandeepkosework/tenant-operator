"""A tenant is pinned to one released chart version and stays on it.

New tenants get the configured default; re-rendering an existing tenant's values file (every
update) keeps the version it already has; only an update that passes chartVersion moves it.

No real Git, Vault or cluster: they are replaced by fakes that record what was asked of them.
The database is a throwaway SQLite file per test.
"""
import types
from unittest import mock

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings, validate_chart_version
from app.database import Base, get_db
from app.main import app
from app.models.tenant import Tenant, TenantStatus
from app.services import chart_versions, helm_values, provisioner
from app.services.git_service import GitServiceError

CLUSTER = types.SimpleNamespace(
    name="spoke-1", region="local", argocd_cluster_server="https://spoke.example:6443",
    vault_server=None, ingress_class_name=None, context="spoke-1-ctx",
)


# ---- the version string ------------------------------------------------------------------

@pytest.mark.parametrize("good", ["v0.1.0", "v1.2.3", "v10.20.30"])
def test_release_tags_are_accepted(good):
    assert validate_chart_version(good) == good


@pytest.mark.parametrize("bad", [
    "main", "0.1.0", "v0.1", "v0.1.0-rc1", "v0.1.0.1", " v0.1.0", "v0.1.0\n", "v0.1.0\nfoo: bar",
    "../v0.1.0", "v0.1.0; rm -rf", "", "v" + "9" * 40 + ".0.0",
])
def test_anything_that_is_not_a_release_tag_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_chart_version(bad)


def test_the_default_is_the_first_release():
    assert get_settings().helm_chart_version == "v0.1.0"


def test_a_bad_default_stops_the_operator_from_starting():
    with pytest.raises(ValidationError):
        Settings(helm_chart_version="main")


# ---- the rendered values file ------------------------------------------------------------

def _render(**kw):
    return helm_values.render_qraie_bridge_values_yaml(
        tenant_name="hbss-x", tenant_slug="hbss-x", environment="stage", namespace="tenant-hbss-x",
        domain="hbss-x.qraie.ai", argocd_cluster_server="https://spoke.example:6443", **kw)


def test_the_version_is_a_top_level_key_next_to_the_other_argocd_fields():
    doc = yaml.safe_load(_render(chart_version="v0.1.0"))
    assert doc["chartVersion"] == "v0.1.0"
    assert doc["argocdClusterServer"] == "https://spoke.example:6443"
    assert doc["tenant"]["id"] == "hbss-x"            # the fields the ApplicationSet already reads are unchanged


def test_without_a_version_the_key_is_absent():
    assert "chartVersion" not in yaml.safe_load(_render())


def test_rendering_uses_the_tenants_own_version_not_the_current_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "helm_chart_version", "v0.9.0")
    t = Tenant(tenant_name="hbss-x", environment="stage", namespace="tenant-hbss-x", domain="hbss-x.qraie.ai",
               helm_chart_version="v0.1.0", disabled_services=[])
    assert yaml.safe_load(provisioner._render_values_yaml(t, CLUSTER))["chartVersion"] == "v0.1.0"


def _no_server(*a, **k):
    raise GitServiceError("could not list the chart repository's tags")


# ---- database and API --------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(provisioner, "SessionLocal", Session)
    s = get_settings()
    # No network: by default the chart repo "cannot be reached", so a new tenant falls back to the setting.
    monkeypatch.setattr(chart_versions.git_service, "list_remote_tags", _no_server)
    monkeypatch.setattr(s, "admin_password", "t")
    monkeypatch.setattr(s, "api_key", "k")
    yield types.SimpleNamespace(Session=Session, client=TestClient(app), headers={"X-API-Key": "k"})
    app.dependency_overrides.clear()


def _make(env, name, chart_version, status=TenantStatus.RUNNING):
    db = env.Session()
    t = Tenant(tenant_name=name, display_name=name, email="a@b.co", domain=f"{name}.qraie.ai", tier="standard",
               environment="stage", app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0,
               database_size="10Gi", status=status, cluster="spoke-1", namespace=f"tenant-{name}",
               helm_chart_version=chart_version, disabled_services=[])
    db.add(t)
    db.commit()
    tid = t.id
    db.close()
    return tid


def _row(env, tid):
    db = env.Session()
    row = db.get(Tenant, tid)
    db.expunge(row)
    db.close()
    return row


def _post_create(env, name, chart_version=None):
    body = {"tenantId": name, "displayName": name, "email": "a@b.co", "password": "changeme123",
            "domain": f"{name}.qraie.ai"}
    if chart_version:
        body["chartVersion"] = chart_version
    with mock.patch("app.api.tenant.preflight.check_tenant_exists", return_value=([], [])), \
            mock.patch("app.api.tenant.provisioner.provision_tenant"):
        return env.client.post("/api/v1/tenant", json=body, headers=env.headers)


def _create(env, name, chart_version=None):
    r = _post_create(env, name, chart_version)
    assert r.status_code == 202, r.text
    return r.json()["tenantId"]


def test_a_new_tenant_is_created_on_the_configured_version(env, monkeypatch):
    tid = _create(env, "hbss-020")
    import uuid
    assert _row(env, uuid.UUID(tid)).helm_chart_version == "v0.1.0"


def test_changing_the_default_only_affects_tenants_created_afterwards(env, monkeypatch):
    import uuid
    first = uuid.UUID(_create(env, "hbss-020"))
    monkeypatch.setattr(get_settings(), "helm_chart_version", "v0.2.0")
    second = uuid.UUID(_create(env, "hbss-021"))
    assert _row(env, first).helm_chart_version == "v0.1.0"      # the existing tenant did not move
    assert _row(env, second).helm_chart_version == "v0.2.0"


def test_the_response_shows_the_pinned_version(env):
    tid = _make(env, "hbss-030", "v0.1.0")
    r = env.client.get(f"/api/v1/tenant/{tid}", headers=env.headers)
    assert r.status_code == 200 and r.json()["helmChartVersion"] == "v0.1.0"


def _put(env, tid, body):
    with mock.patch("app.api.tenant.provisioner.update_tenant") as upd:
        r = env.client.put(f"/api/v1/tenant/{tid}", json=body, headers=env.headers)
    return r, upd


def test_an_update_without_chartVersion_keeps_the_tenant_where_it_is(env):
    tid = _make(env, "hbss-031", "v0.1.0")
    r, upd = _put(env, tid, {"users": 5})
    assert r.status_code == 200, r.text
    assert upd.call_args.args[-1] is None                       # no new chart version requested


def test_an_update_can_move_a_tenant_to_a_new_release(env):
    tid = _make(env, "hbss-032", "v0.1.0")
    r, upd = _put(env, tid, {"chartVersion": "v0.1.1"})
    assert r.status_code == 200, r.text
    assert upd.call_args.args[-1] == "v0.1.1"


def test_an_unpinned_tenant_cannot_be_updated_until_it_is_pinned(env):
    tid = _make(env, "hbss-033", None)
    r, upd = _put(env, tid, {"users": 5})
    assert r.status_code == 409 and "pinned chart version" in r.json()["detail"]
    assert not upd.called                                       # nothing was queued, nothing re-rendered


def test_an_unpinned_tenant_is_pinned_by_naming_the_release_it_runs(env):
    tid = _make(env, "hbss-034", None)
    r, upd = _put(env, tid, {"chartVersion": "v0.1.0"})
    assert r.status_code == 200, r.text
    assert upd.call_args.args[-1] == "v0.1.0"


@pytest.mark.parametrize("bad", ["main", "v0.1", "0.1.0", "v0.1.0-rc1"])
def test_the_api_rejects_anything_that_is_not_a_release_tag(env, bad):
    tid = _make(env, "hbss-035", "v0.1.0")
    r, upd = _put(env, tid, {"chartVersion": bad})
    assert r.status_code == 422 and not upd.called


# ---- what the update actually commits ----------------------------------------------------

@pytest.fixture
def committed(env, monkeypatch):
    """Runs provisioner.update_tenant against fakes and returns the values files it committed."""
    class Files(list):
        on_disk = None
    files = Files()
    files_on_disk = files.on_disk = {}          # slug -> the file's current text, for the read of the existing file
    monkeypatch.setattr(provisioner.cluster_selector, "load_cluster_registry", lambda: [CLUSTER])
    monkeypatch.setattr(provisioner.git_service, "commit_tenant_manifest",
                        lambda slug, content, message, tenants_dir=None: files.append((slug, content, message)) or "sha1")
    monkeypatch.setattr(provisioner.git_service, "read_tenant_manifest",
                        lambda slug, tenants_dir=None: files_on_disk.get(slug))
    monkeypatch.setattr(provisioner, "_wait_for_argocd_and_k8s", lambda *a, **k: None)
    monkeypatch.setattr(provisioner, "_commit_tenant_info", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.tenant_cr, "echo_cr_phase", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.status_bus, "publish", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.mongo_service, "upsert_registry_record", lambda *a, **k: None)
    return files


def test_an_update_re_renders_the_tenants_own_version_even_if_the_default_moved(env, committed, monkeypatch):
    monkeypatch.setattr(get_settings(), "helm_chart_version", "v0.9.0")       # the default has moved on
    tid = _make(env, "hbss-040", "v0.1.0")
    provisioner.update_tenant(tid, None, 7, None, None, None)                 # a change that has nothing to do with the chart
    assert len(committed) == 1
    assert yaml.safe_load(committed[0][1])["chartVersion"] == "v0.1.0"        # it did NOT jump to v0.9.0
    assert _row(env, tid).helm_chart_version == "v0.1.0"


def test_an_update_with_a_new_chartVersion_moves_only_that_tenant(env, committed):
    a = _make(env, "hbss-041", "v0.1.0")
    b = _make(env, "hbss-042", "v0.1.0")
    provisioner.update_tenant(a, None, None, None, None, "v0.1.1")
    assert yaml.safe_load(committed[0][1])["chartVersion"] == "v0.1.1"
    assert "chart=v0.1.1" in committed[0][2]                                  # visible in the git history
    assert _row(env, a).helm_chart_version == "v0.1.1"
    assert _row(env, b).helm_chart_version == "v0.1.0"                        # the other tenant did not move


def test_the_provisioner_also_refuses_an_unpinned_tenant(env, committed):
    tid = _make(env, "hbss-043", None)
    provisioner.update_tenant(tid, None, 3, None, None, None)
    assert committed == []                                                    # nothing was written to git
    assert _row(env, tid).helm_chart_version is None
    assert _row(env, tid).status == TenantStatus.RUNNING                      # and it was not left in UPDATING


# ---- per-tenant overrides survive a re-render ---------------------------------------------

HAND_EDITED = """argocdClusterServer: https://old
chartVersion: v0.1.0
tenant: {id: hbss-050, name: hbss-050, namespace: tenant-hbss-050, domain: hbss-050.qraie.ai}
imageTags:
  bridge: 9.9.9
replicaOverrides:
  qraie-ui: 0
resourceOverrides:
  bridge:
    limits: {memory: 4Gi}
"""


def test_an_update_keeps_the_overrides_added_by_hand(env, committed):
    tid = _make(env, "hbss-050", "v0.1.0")
    committed.on_disk["hbss-050"] = HAND_EDITED
    provisioner.update_tenant(tid, None, 7, None, None, None)
    out = yaml.safe_load(committed[0][1])
    assert out["imageTags"] == {"bridge": "9.9.9"}
    assert out["replicaOverrides"] == {"qraie-ui": 0}                         # zero survives, it is not "empty"
    assert out["resourceOverrides"] == {"bridge": {"limits": {"memory": "4Gi"}}}
    assert out["argocdClusterServer"] == CLUSTER.argocd_cluster_server        # operator-owned keys still re-rendered
    assert out["tenant"]["domain"] == "hbss-050.qraie.ai"


def test_an_update_of_a_tenant_without_overrides_is_unchanged(env, committed):
    tid = _make(env, "hbss-051", "v0.1.0")
    committed.on_disk["hbss-051"] = "argocdClusterServer: https://old\nchartVersion: v0.1.0\n"
    provisioner.update_tenant(tid, None, 7, None, None, None)
    assert not any(k in yaml.safe_load(committed[0][1]) for k in helm_values.OVERRIDE_KEYS)


def test_other_keys_in_the_old_file_are_not_copied():
    out = helm_values.carry_over_overrides("a: 1\n", "a: 2\nsomethingElse: x\nimageTags: {bridge: '1'}\n")
    assert yaml.safe_load(out) == {"a": 1, "imageTags": {"bridge": "1"}}


def test_a_file_that_cannot_be_read_stops_the_update_instead_of_dropping_overrides(env, committed):
    tid = _make(env, "hbss-052", "v0.1.0")
    committed.on_disk["hbss-052"] = "imageTags: [unclosed\n"
    provisioner.update_tenant(tid, None, 7, None, None, None)
    assert committed == []                                                    # nothing was overwritten
    assert _row(env, tid).status == TenantStatus.FAILED


def test_a_brand_new_tenant_has_no_overrides_block():
    assert helm_values.carry_over_overrides("a: 1\n", None) == "a: 1\n"


# ---- a new tenant gets the newest release unless it asks for another ------------------------

def _tags(monkeypatch, tags):
    monkeypatch.setattr(chart_versions.git_service, "list_remote_tags", lambda *a, **k: list(tags))


def _version_of(env, tid):
    import uuid
    return _row(env, uuid.UUID(tid)).helm_chart_version


def test_a_new_tenant_gets_the_newest_release_by_default(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.0", "v0.1.1"])
    assert _version_of(env, _create(env, "hbss-060")) == "v0.1.1"


def test_newest_means_highest_number_not_alphabetical_last(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.9", "v0.1.10", "v0.1.2", "v0.9.0"])
    assert _version_of(env, _create(env, "hbss-061")) == "v0.9.0"
    _tags(monkeypatch, ["v0.1.9", "v0.1.10", "v0.1.2"])
    assert _version_of(env, _create(env, "hbss-062")) == "v0.1.10"


def test_a_new_release_is_picked_up_without_changing_the_operator(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.0"])
    first = _create(env, "hbss-063")
    _tags(monkeypatch, ["v0.1.0", "v0.1.1"])                    # someone cuts a release
    second = _create(env, "hbss-064")
    assert _version_of(env, first) == "v0.1.0"                  # the running tenant did not move
    assert _version_of(env, second) == "v0.1.1"


def test_an_older_release_can_be_asked_for(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.0", "v0.1.1"])
    assert _version_of(env, _create(env, "hbss-065", "v0.1.0")) == "v0.1.0"


def test_a_release_that_does_not_exist_is_refused_and_nothing_is_created(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.0", "v0.1.1"])
    r = _post_create(env, "hbss-066", "v0.7.0")
    assert r.status_code == 400
    assert "v0.7.0" in r.text and "v0.1.1" in r.text            # says what is available
    db = env.Session()
    assert db.query(Tenant).count() == 0
    db.close()


def test_a_named_release_is_refused_when_it_cannot_be_checked(env):
    r = _post_create(env, "hbss-067", "v0.1.0")                 # the fixture's repo is unreachable
    assert r.status_code == 503


def test_without_a_reachable_repo_a_new_tenant_falls_back_to_the_default(env):
    assert _version_of(env, _create(env, "hbss-068")) == "v0.1.0"


def test_a_repo_with_no_release_falls_back_to_the_default(env, monkeypatch):
    _tags(monkeypatch, [])
    monkeypatch.setattr(get_settings(), "helm_chart_version", "v0.3.0")
    assert _version_of(env, _create(env, "hbss-069")) == "v0.3.0"


def test_latest_can_be_switched_off(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.0", "v0.1.1"])
    monkeypatch.setattr(get_settings(), "helm_chart_use_latest", False)
    assert _version_of(env, _create(env, "hbss-070")) == "v0.1.0"


def test_an_update_to_a_release_that_does_not_exist_is_refused(env, monkeypatch):
    _tags(monkeypatch, ["v0.1.0", "v0.1.1"])
    tid = _make(env, "hbss-071", "v0.1.0")
    with mock.patch("app.api.tenant.provisioner.update_tenant") as upd:
        bad = env.client.put(f"/api/v1/tenant/{tid}", json={"chartVersion": "v0.5.0"}, headers=env.headers)
        good = env.client.put(f"/api/v1/tenant/{tid}", json={"chartVersion": "v0.1.1"}, headers=env.headers)
    assert bad.status_code == 400
    assert good.status_code == 200
    assert upd.call_count == 1                                  # only the valid request was applied


def test_an_update_is_not_blocked_when_the_repo_cannot_be_read(env):
    tid = _make(env, "hbss-072", "v0.1.0")
    with mock.patch("app.api.tenant.provisioner.update_tenant") as upd:
        r = env.client.put(f"/api/v1/tenant/{tid}", json={"chartVersion": "v0.1.1"}, headers=env.headers)
    assert r.status_code == 200 and upd.call_count == 1


def test_the_create_request_rejects_anything_that_is_not_a_release_tag(env):
    for bad in ["main", "0.1.1", "v0.1", "v0.1.1\nfoo: bar"]:
        assert _post_create(env, "hbss-073", bad).status_code == 422


# ---- reading the tags ----------------------------------------------------------------------

def _ls_remote(monkeypatch, output):
    from app.services import git_service
    fake = mock.Mock()
    fake.ls_remote.return_value = output
    monkeypatch.setattr(git_service.git, "Git", lambda: fake)
    return fake


def test_only_release_tags_are_listed(monkeypatch):
    from app.services import git_service
    _ls_remote(monkeypatch, "\n".join([
        "a" * 40 + "\trefs/tags/v0.1.0", "b" * 40 + "\trefs/tags/v0.1.1", "c" * 40 + "\trefs/tags/v0.2.0-rc1",
        "d" * 40 + "\trefs/tags/latest", "e" * 40 + "\trefs/tags/0.3.0", "f" * 40 + "\trefs/tags/v1.0.0",
    ]))
    assert git_service.list_remote_tags("http://x/y.git") == ["v0.1.0", "v0.1.1", "v1.0.0"]


def test_a_failed_lookup_never_leaks_the_token(monkeypatch):
    from git import GitCommandError
    from app.services import git_service
    fake = _ls_remote(monkeypatch, "")
    fake.ls_remote.side_effect = GitCommandError("ls-remote", 128, stderr="fatal: unable to access 'http://gitadmin:SECRET@host/r.git/'")
    with pytest.raises(git_service.GitServiceError) as e:
        git_service.list_remote_tags("http://gitadmin:SECRET@host/r.git")
    assert "SECRET" not in str(e.value)


@pytest.mark.parametrize("repo, expected", [
    ("http://gitadmin:tok@gitea.gitops.svc.cluster.local:3000/gitadmin/k8s-infra-setup.git",
     "http://gitadmin:tok@gitea.gitops.svc.cluster.local:3000/gitadmin/helm-chart-bridge.git"),
    ("https://github.com/acme/tenant-config.git", "https://github.com/acme/helm-chart-bridge.git"),
    ("git@github.com:acme/tenant-config.git", "git@github.com:acme/helm-chart-bridge.git"),
])
def test_the_chart_repo_is_the_sibling_of_the_values_repo(monkeypatch, repo, expected):
    from app.services import git_service
    s = get_settings()
    monkeypatch.setattr(s, "git_repo_url", repo)
    monkeypatch.setattr(s, "helm_chart_repo_url", None)
    monkeypatch.setattr(s, "git_https_token", None)
    assert git_service.chart_repo_url() == expected


def test_an_explicit_chart_repo_url_wins_and_gets_the_token(monkeypatch):
    from app.services import git_service
    s = get_settings()
    monkeypatch.setattr(s, "helm_chart_repo_url", "https://git.example/o/charts.git")
    monkeypatch.setattr(s, "git_https_token", "tok")
    monkeypatch.setattr(s, "git_https_username", "bot")
    assert git_service.chart_repo_url() == "https://bot:tok@git.example/o/charts.git"
