"""Archive and restore a tenant, and why a running tenant is archived before it is deleted.

Archiving commits `archived: true` for the tenant (the chart then runs no pods and no ingress) and
keeps everything else; restoring commits the file without it. A permanent delete runs only from
ARCHIVED (or FAILED), unless forced.

No real Git, Argo CD, Kubernetes or Vault: they are replaced by fakes that record what was asked.
The database is a throwaway SQLite file per test.
"""
import types
from unittest import mock

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import database
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models.tenant import Tenant, TenantStatus
from app.services import helm_values, provisioner

CLUSTER = types.SimpleNamespace(
    name="spoke-1", region="local", argocd_cluster_server="https://spoke.example:6443",
    vault_server=None, ingress_class_name=None, context="spoke-1-ctx",
)


# ---- rendering ---------------------------------------------------------------------------

def _render(**kw):
    args = dict(tenant_name="acme", tenant_slug="acme", environment="stage", namespace="tenant-acme",
                domain="acme.qraie.ai", argocd_cluster_server="https://spoke.example:6443", chart_version="v0.1.3")
    args.update(kw)
    return yaml.safe_load(helm_values.render_qraie_bridge_values_yaml(**args))


def test_an_archived_tenant_gets_the_archived_flag():
    assert _render(archived=True)["archived"] is True


def test_a_normal_tenant_has_no_archived_key():
    assert "archived" not in _render()


@pytest.mark.parametrize("version,ok", [("v0.1.3", True), ("v0.1.10", True), ("v0.2.0", True),
                                        ("v0.1.2", False), ("v0.1.0", False), (None, False)])
def test_only_a_chart_that_understands_archived_can_be_archived(version, ok):
    assert helm_values.supports_archive(version, "v0.1.3") is ok


# ---- API ---------------------------------------------------------------------------------

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
    monkeypatch.setattr(s, "admin_password", "t")
    monkeypatch.setattr(s, "api_key", "k")
    monkeypatch.setattr(s, "archive_min_chart_version", "v0.1.3")
    yield types.SimpleNamespace(Session=Session, client=TestClient(app), headers={"X-API-Key": "k"})
    app.dependency_overrides.clear()


def _make(env, name, status=TenantStatus.RUNNING, chart_version="v0.1.3"):
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


def _call(env, method, path, task):
    with mock.patch(f"app.api.tenant.provisioner.{task}") as fn:
        r = env.client.request(method, f"/api/v1/tenant/{path}", headers=env.headers)
    return r, fn


def test_a_running_tenant_on_a_new_enough_chart_is_archived(env):
    tid = _make(env, "hbss-040")
    r, fn = _call(env, "POST", f"{tid}/archive", "archive_tenant")
    assert r.status_code == 202 and r.json()["status"] == "ARCHIVING"
    fn.assert_called_once_with(tid)


def test_a_tenant_on_an_old_chart_is_not_archived(env):
    tid = _make(env, "hbss-041", chart_version="v0.1.2")
    r, fn = _call(env, "POST", f"{tid}/archive", "archive_tenant")
    assert r.status_code == 409 and "does not support archiving" in r.json()["detail"]
    assert not fn.called                           # it would have reported success with the pods still running


@pytest.mark.parametrize("status", [TenantStatus.FAILED, TenantStatus.SYNCING, TenantStatus.ARCHIVED, TenantStatus.DELETED])
def test_only_a_running_tenant_can_be_archived(env, status):
    tid = _make(env, f"hbss-{status.value.lower()}", status=status)
    r, fn = _call(env, "POST", f"{tid}/archive", "archive_tenant")
    assert r.status_code == 409 and not fn.called


def test_an_archived_tenant_is_restored(env):
    tid = _make(env, "hbss-042", status=TenantStatus.ARCHIVED)
    r, fn = _call(env, "POST", f"{tid}/restore", "restore_tenant")
    assert r.status_code == 202 and r.json()["status"] == "RESTORING"
    fn.assert_called_once_with(tid)


def test_a_running_tenant_cannot_be_restored(env):
    tid = _make(env, "hbss-043")
    r, fn = _call(env, "POST", f"{tid}/restore", "restore_tenant")
    assert r.status_code == 409 and not fn.called


def test_a_running_tenant_must_be_archived_before_it_is_deleted(env):
    tid = _make(env, "hbss-044")
    r, fn = _call(env, "DELETE", str(tid), "delete_tenant")
    assert r.status_code == 409 and "Archive it first" in r.json()["detail"]
    assert not fn.called


def test_force_deletes_a_running_tenant_directly(env):
    tid = _make(env, "hbss-045")
    r, fn = _call(env, "DELETE", f"{tid}?force=true", "delete_tenant")
    assert r.status_code == 202 and fn.called


@pytest.mark.parametrize("status", [TenantStatus.ARCHIVED, TenantStatus.FAILED])
def test_an_archived_or_failed_tenant_can_be_deleted(env, status):
    tid = _make(env, f"hbss-del-{status.value.lower()}", status=status)
    r, fn = _call(env, "DELETE", str(tid), "delete_tenant")
    assert r.status_code == 202 and fn.called


@pytest.mark.parametrize("status", [TenantStatus.ARCHIVING, TenantStatus.RESTORING, TenantStatus.DELETING])
def test_a_tenant_in_the_middle_of_a_change_cannot_be_deleted(env, status):
    tid = _make(env, f"hbss-mid-{status.value.lower()}", status=status)
    r, fn = _call(env, "DELETE", str(tid), "delete_tenant")
    assert r.status_code == 409 and not fn.called


def test_the_response_shows_when_a_tenant_was_archived(env):
    tid = _make(env, "hbss-046", status=TenantStatus.ARCHIVED)
    r = env.client.get(f"/api/v1/tenant/{tid}", headers=env.headers)
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED" and r.json()["archivedAt"] is None


# ---- what the background tasks do --------------------------------------------------------

@pytest.fixture
def flow(env, monkeypatch):
    """Fake out everything outside the database and record the files committed to git."""
    committed = []
    monkeypatch.setattr(provisioner.cluster_selector, "load_cluster_registry", lambda: [CLUSTER])
    monkeypatch.setattr(provisioner.git_service, "read_tenant_manifest", lambda slug, tenants_dir=None: "chartVersion: v0.1.3\n")
    monkeypatch.setattr(provisioner.git_service, "commit_tenant_manifest",
                        lambda slug, content, message, tenants_dir=None: committed.append((message, content)) or "abc123")
    monkeypatch.setattr(provisioner, "_with_service_config", lambda tenant, values_yaml, existing, old: values_yaml)
    monkeypatch.setattr(provisioner, "_commit_tenant_info", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.spoke_cr, "upsert_spoke_cluster_cr", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.tenant_cr, "echo_cr_phase", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.status_bus, "publish", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.mongo_service, "upsert_registry_record", lambda *a, **k: None)
    monkeypatch.setattr(provisioner.argocd_service, "get_application_status", lambda slug: {"health": "Healthy"})
    monkeypatch.setattr(provisioner.argocd_service, "classify", lambda status: "healthy")
    monkeypatch.setattr(provisioner.kubernetes_service, "all_scaled_down", lambda *a, **k: True)
    monkeypatch.setattr(provisioner.time, "sleep", lambda s: None)
    env.committed = committed
    return env


def _row(env, tid):
    db = env.Session()
    row = db.get(Tenant, tid)
    db.expunge(row)
    db.close()
    return row


def test_archiving_commits_the_flag_and_ends_archived(flow):
    tid = _make(flow, "hbss-050")
    provisioner.archive_tenant(tid)
    row = _row(flow, tid)
    assert row.status == TenantStatus.ARCHIVED and row.archived_at is not None
    message, content = flow.committed[-1]
    assert "archive hbss-050" in message and yaml.safe_load(content)["archived"] is True


def test_archiving_waits_until_the_pods_are_gone(flow, monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(provisioner.kubernetes_service, "all_scaled_down", lambda *a, **k: next(answers))
    tid = _make(flow, "hbss-051")
    provisioner.archive_tenant(tid)
    assert _row(flow, tid).status == TenantStatus.ARCHIVED


def test_a_failure_while_archiving_marks_the_tenant_failed(flow, monkeypatch):
    monkeypatch.setattr(provisioner.git_service, "commit_tenant_manifest", mock.Mock(side_effect=RuntimeError("git down")))
    tid = _make(flow, "hbss-052")
    provisioner.archive_tenant(tid)
    row = _row(flow, tid)
    assert row.status == TenantStatus.FAILED and "git down" in row.error_message and row.archived_at is None


def test_restoring_commits_the_file_without_the_flag(flow, monkeypatch):
    def running(db, tenant, context):
        provisioner._set_status(db, tenant, TenantStatus.RUNNING)
    monkeypatch.setattr(provisioner, "_wait_for_argocd_and_k8s", running)
    tid = _make(flow, "hbss-053", status=TenantStatus.ARCHIVED)
    db = flow.Session()
    row = db.get(Tenant, tid)
    from datetime import datetime, timezone
    row.archived_at = datetime.now(timezone.utc)
    db.commit()
    db.close()

    provisioner.restore_tenant(tid)
    row = _row(flow, tid)
    assert row.status == TenantStatus.RUNNING and row.archived_at is None
    message, content = flow.committed[-1]
    assert "restore hbss-053" in message and "archived" not in yaml.safe_load(content)


def test_an_update_to_an_archived_tenant_keeps_it_archived(flow):
    tid = _make(flow, "hbss-054", status=TenantStatus.ARCHIVED)
    db = flow.Session()
    row = db.get(Tenant, tid)
    from datetime import datetime, timezone
    row.archived_at = datetime.now(timezone.utc)
    db.commit()
    rendered = provisioner._render_values_yaml(row, CLUSTER)
    db.close()
    assert yaml.safe_load(rendered)["archived"] is True


# ---- an existing database ----------------------------------------------------------------

def test_an_existing_database_gets_the_new_column_at_startup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, tenant_name VARCHAR(63))"))
    monkeypatch.setattr(database, "engine", engine)

    database.ensure_schema()
    database.ensure_schema()                                    # running it again changes nothing

    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tenants)"))}
    assert "archived_at" in cols


def _deployment(spec_replicas, status_replicas):
    return types.SimpleNamespace(spec=types.SimpleNamespace(replicas=spec_replicas),
                                 status=types.SimpleNamespace(replicas=status_replicas))


def _scaled_down(monkeypatch, deployments):
    """all_scaled_down() against a namespace whose chart Deployments are as given."""
    from app.services import kubernetes_service as k8s
    seen = {}

    class FakeApps:
        def list_namespaced_deployment(self, namespace, label_selector):
            seen["selector"] = label_selector
            return types.SimpleNamespace(items=deployments)

    monkeypatch.setattr(k8s, "namespace_exists", lambda *a: True)
    monkeypatch.setattr(k8s, "_get_api_client", lambda ctx: None)
    monkeypatch.setattr(k8s.client, "AppsV1Api", lambda api: FakeApps())
    return k8s.all_scaled_down("ctx", "tenant-x", "x"), seen


def test_stopped_means_every_chart_deployment_is_at_zero(monkeypatch):
    ok, seen = _scaled_down(monkeypatch, [_deployment(0, 0), _deployment(0, None)])
    assert ok is True
    assert seen["selector"] == "app.kubernetes.io/instance=x"   # only the chart's own Deployments


def test_a_deployment_still_scaling_down_is_not_stopped(monkeypatch):
    assert _scaled_down(monkeypatch, [_deployment(0, 0), _deployment(0, 1)])[0] is False   # a pod is still going
    assert _scaled_down(monkeypatch, [_deployment(1, 1)])[0] is False                       # not scaled down yet


def test_ereps_are_never_looked_at_and_never_deleted(monkeypatch):
    from app.services import kubernetes_service as k8s
    assert not hasattr(k8s, "delete_erep_workloads")            # archiving does not delete them
    assert _scaled_down(monkeypatch, [])[0] is True              # a namespace with only ereps in it is "stopped"
