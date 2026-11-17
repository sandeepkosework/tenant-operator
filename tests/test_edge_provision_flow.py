"""The whole create and delete flow with the edge feature OFF (the default) and ON.

Regression guard for edge routing: with EDGE_ENABLED=false a tenant must be created and deleted exactly
as before, and with it on, nothing about the edge may ever stop a tenant being created or deleted
unless EDGE_REQUIRED is set. Everything outside the operator (Git, Argo CD, Kubernetes, Vault, Mongo,
SSH) is replaced by fakes; the database is a throwaway SQLite file.
"""
import types
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_event import TenantEvent
from app.services import (
    cluster_selector, edge_service, erep_setup, git_service, kubernetes_service, mongo_service,
    onboarding_service, preflight, progress, provisioner, spoke_cr, spoke_scaler, status_bus, tenant_cr, vault_service,
)

VIP = "192.168.85.207:443"
CLUSTER = cluster_selector.ClusterInfo(name="spoke-1", context="spoke-1-ctx", argocd_cluster_server="https://s:6443",
                                       max_tenants=1000, environments=["stage"], edge_upstream=VIP)


class Edge:
    def __init__(self):
        self.commands, self.routes, self.error = [], {}, None
        self.dns = set()                               # the tenants' A records "at GoDaddy"

    def __call__(self, command):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        p = command.split()
        if p[0] == "dns-add":
            self.dns.add(p[1])
            return 0, '{"ok": true, "host": "%s", "result": "added"}\n' % p[1]
        if p[0] == "dns-remove":
            self.dns.discard(p[1])
            return 0, '{"ok": true, "host": "%s", "result": "removed"}\n' % p[1]
        if p[0] == "add":
            self.routes[p[1]] = p[2]
            return 0, '{"ok": true, "host": "%s", "result": "added"}\n' % p[1]
        if p[0] == "remove":
            self.routes.pop(p[1], None)
            return 0, '{"ok": true, "host": "%s", "result": "removed"}\n' % p[1]
        return 0, '{"ok": true, "hosts": []}\n'


@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(provisioner, "SessionLocal", Session)
    monkeypatch.setattr(progress, "SessionLocal", Session)

    calls = []                                          # the order things happened in
    s = get_settings()
    monkeypatch.setattr(s, "erep_setup_enabled", True)
    monkeypatch.setattr(s, "edge_enabled", False)
    monkeypatch.setattr(s, "edge_required", False)
    monkeypatch.setattr(s, "edge_dns_enabled", False)
    monkeypatch.setattr(s, "edge_attempts", 1)
    monkeypatch.setattr(s, "edge_retry_delay_seconds", 0)
    monkeypatch.setattr(s, "onboarding_server_url", None)
    monkeypatch.setattr(s, "provisioning_poll_interval_seconds", 0)

    monkeypatch.setattr(cluster_selector, "select_cluster", lambda env, db: (CLUSTER, 1))
    monkeypatch.setattr(cluster_selector, "load_cluster_registry", lambda: [CLUSTER])
    monkeypatch.setattr(preflight, "check_tenant_resources", lambda t, ctx: [])
    monkeypatch.setattr(spoke_scaler, "maybe_trigger_spoke_scale", lambda *a, **k: None)
    monkeypatch.setattr(spoke_cr, "upsert_spoke_cluster_cr", lambda *a, **k: {})
    monkeypatch.setattr(tenant_cr, "build_tenant_cr", lambda t, c: {})
    monkeypatch.setattr(tenant_cr, "create_tenant_cr", lambda cr: ("cr-1", "uid-1"))
    monkeypatch.setattr(tenant_cr, "echo_cr_phase", lambda *a, **k: None)
    monkeypatch.setattr(vault_service, "write_initial_qraie_bridge_tenant_secrets", lambda *a, **k: {"svc": {}})
    monkeypatch.setattr(mongo_service, "write_tenant_env_config", lambda *a, **k: None)
    monkeypatch.setattr(mongo_service, "upsert_registry_record", lambda *a, **k: None)
    monkeypatch.setattr(onboarding_service, "register_tenant", lambda *a, **k: None)
    monkeypatch.setattr(status_bus, "publish", lambda *a, **k: None)
    monkeypatch.setattr(git_service, "commit_tenant_manifest", lambda *a, **k: "abc12345")
    monkeypatch.setattr(git_service, "delete_tenant_manifest", lambda *a, **k: None)
    monkeypatch.setattr(kubernetes_service, "delete_namespace", lambda *a, **k: None)
    monkeypatch.setattr(kubernetes_service, "namespace_fully_deleted", lambda *a, **k: True)
    monkeypatch.setattr(provisioner, "_render_values_yaml", lambda *a, **k: "values")
    monkeypatch.setattr(provisioner, "_with_service_config", lambda t, v, e, o: v)
    monkeypatch.setattr(provisioner, "_commit_tenant_info", lambda *a, **k: None)
    monkeypatch.setattr(provisioner, "_seed_tenant_databases", lambda *a, **k: None)

    def wait_ready(db, tenant, ctx):                    # the pods came up
        provisioner._set_status(db, tenant, TenantStatus.RUNNING)
    monkeypatch.setattr(provisioner, "_wait_for_argocd_and_k8s", wait_ready)
    monkeypatch.setattr(erep_setup, "setup_default_erep", lambda t: calls.append("erep"))

    edge = Edge()
    monkeypatch.setattr(edge_service, "_ssh_exec", edge)
    return types.SimpleNamespace(Session=Session, edge=edge, calls=calls, settings=s, monkeypatch=monkeypatch)


def _new_tenant(w, name="hbssf", status=TenantStatus.PENDING):
    db = w.Session()
    t = Tenant(tenant_name=name, display_name=name, email="a@b.co", domain=f"{name}.qraie.ai", tier="standard",
               environment="stage", app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0,
               database_size="10Gi", status=status, cluster="spoke-1" if status != TenantStatus.PENDING else None,
               namespace=f"tenant-{name}" if status != TenantStatus.PENDING else None,
               helm_chart_version="v0.1.8", disabled_services=[])
    db.add(t)
    db.commit()
    tid = t.id
    db.close()
    return tid


def _result(w, tid):
    db = w.Session()
    t = db.get(Tenant, tid)
    events = [(e.step, e.state) for e in db.query(TenantEvent).filter(TenantEvent.tenant_id == tid).order_by(TenantEvent.id).all()]
    out = (t.status, t.error_message, events)
    db.close()
    return out


def _step_states(events, step):
    return [st for s, st in events if s == step]


# ---- create ------------------------------------------------------------------------------

def test_create_with_edge_off_is_exactly_as_before(world):
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and err is None
    assert world.edge.commands == []                                # nothing was sent anywhere
    assert _step_states(events, "edge") == ["skip"]                 # the only visible difference: one skipped step
    assert world.calls == ["erep"] and _step_states(events, "ready") == ["done"]
    for step in ("validate", "placement", "preflight", "register", "secrets", "manifest", "erep"):
        assert "done" in _step_states(events, step), step


def test_create_with_edge_on_publishes_the_route_before_the_erep(world):
    world.settings.edge_enabled = True
    world.monkeypatch.setattr(edge_service, "_ssh_exec", lambda c: (world.calls.append("route") or world.edge(c)))
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and err is None
    assert world.edge.routes == {"hbssf.qraie.ai": VIP}
    assert world.calls == ["route", "erep"]                         # the eRep calls the public URL, so the route comes first
    assert _step_states(events, "edge") == ["start", "done"]


@pytest.mark.parametrize("boom", [
    edge_service.EdgeError("cannot reach the edge host: timed out"),
    RuntimeError("something the ssh library did not document"),
    EOFError(),
])
def test_an_edge_problem_never_stops_a_tenant_being_created(world, boom):
    world.settings.edge_enabled = True
    world.edge.error = boom
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and err is None
    assert _step_states(events, "edge")[-1] == "warn"
    assert world.calls == ["erep"]                                  # the rest of the flow still ran
    assert _step_states(events, "ready") == ["done"]


def test_edge_required_fails_the_tenant_and_skips_what_follows(world):
    world.settings.edge_enabled = True
    world.settings.edge_required = True
    world.edge.error = edge_service.EdgeError("cannot reach the edge host")
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, err, _events = _result(world, tid)
    assert status == TenantStatus.FAILED and "edge route failed" in err
    assert world.calls == []                                        # no eRep for a failed tenant


def test_a_spoke_with_no_upstream_does_not_fail_the_tenant(world):
    world.settings.edge_enabled = True
    world.monkeypatch.setattr(cluster_selector, "select_cluster",
                              lambda env, db: (cluster_selector.ClusterInfo(name="spoke-1", context="c", argocd_cluster_server="x",
                                                                            max_tenants=1, environments=["stage"]), 1))
    world.monkeypatch.setattr(world.settings, "edge_default_upstream", None)
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, _err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and _step_states(events, "edge")[-1] == "warn"
    assert world.edge.commands == []                                # an address is never guessed


def test_create_with_dns_on_makes_the_record_after_the_route_and_before_the_erep(world):
    world.settings.edge_enabled = True
    world.settings.edge_dns_enabled = True
    world.monkeypatch.setattr(edge_service, "_ssh_exec", lambda c: (world.calls.append(c.split()[0]) or world.edge(c)))
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and err is None
    assert world.calls == ["add", "dns-add", "erep"]
    assert world.edge.routes == {"hbssf.qraie.ai": VIP} and world.edge.dns == {"hbssf.qraie.ai"}
    assert _step_states(events, "dns") == ["start", "done"]


def test_create_with_dns_off_is_unchanged_apart_from_one_skipped_step(world):
    world.settings.edge_enabled = True                              # routes on, DNS off
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, _err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and world.edge.dns == set()
    assert not any(c.startswith("dns") for c in world.edge.commands)
    assert _step_states(events, "dns") == ["skip"]


@pytest.mark.parametrize("boom", [edge_service.EdgeError("godaddy refused the API key (HTTP 403)"), RuntimeError("odd")])
def test_a_dns_problem_never_stops_a_tenant_being_created(world, boom):
    world.settings.edge_enabled = True
    world.settings.edge_dns_enabled = True

    def ssh(command):
        if command.startswith("dns"):
            raise boom
        return world.edge(command)
    world.monkeypatch.setattr(edge_service, "_ssh_exec", ssh)
    tid = _new_tenant(world)
    provisioner.provision_tenant(tid, "pw")
    status, err, events = _result(world, tid)
    assert status == TenantStatus.RUNNING and err is None
    assert world.edge.routes == {"hbssf.qraie.ai": VIP}             # the route is still there
    assert _step_states(events, "dns")[-1] == "warn" and world.calls == ["erep"]


# ---- delete ------------------------------------------------------------------------------

def test_delete_with_edge_off_is_exactly_as_before(world):
    tid = _new_tenant(world, status=TenantStatus.RUNNING)
    provisioner.delete_tenant(tid)
    status, err, events = _result(world, tid)
    assert status == TenantStatus.DELETED and err is None
    assert world.edge.commands == [] and _step_states(events, "edge") == []   # not even an event


def test_delete_with_edge_on_removes_the_route(world):
    world.settings.edge_enabled = True
    world.edge.routes["hbssf.qraie.ai"] = VIP
    tid = _new_tenant(world, status=TenantStatus.RUNNING)
    provisioner.delete_tenant(tid)
    status, _err, _events = _result(world, tid)
    assert status == TenantStatus.DELETED and world.edge.routes == {}


@pytest.mark.parametrize("boom", [edge_service.EdgeError("down"), RuntimeError("odd")])
def test_an_edge_problem_never_stops_a_tenant_being_deleted(world, boom):
    world.settings.edge_enabled = True
    world.edge.error = boom
    tid = _new_tenant(world, status=TenantStatus.RUNNING)
    provisioner.delete_tenant(tid)
    status, err, events = _result(world, tid)
    assert status == TenantStatus.DELETED and err is None
    assert _step_states(events, "edge")[-1] == "warn"


def test_delete_with_dns_on_removes_the_route_and_the_record(world):
    world.settings.edge_enabled = True
    world.settings.edge_dns_enabled = True
    world.edge.routes["hbssf.qraie.ai"] = VIP
    world.edge.dns.add("hbssf.qraie.ai")
    tid = _new_tenant(world, status=TenantStatus.RUNNING)
    provisioner.delete_tenant(tid)
    status, _err, _events = _result(world, tid)
    assert status == TenantStatus.DELETED and world.edge.routes == {} and world.edge.dns == set()


@pytest.mark.parametrize("boom", [edge_service.EdgeError("down"), RuntimeError("odd")])
def test_a_dns_problem_never_stops_a_tenant_being_deleted(world, boom):
    world.settings.edge_enabled = True
    world.settings.edge_dns_enabled = True
    world.edge.routes["hbssf.qraie.ai"] = VIP

    def ssh(command):
        if command.startswith("dns"):
            raise boom
        return world.edge(command)
    world.monkeypatch.setattr(edge_service, "_ssh_exec", ssh)
    tid = _new_tenant(world, status=TenantStatus.RUNNING)
    provisioner.delete_tenant(tid)
    status, err, events = _result(world, tid)
    assert status == TenantStatus.DELETED and err is None and world.edge.routes == {}
    assert _step_states(events, "dns")[-1] == "warn"


def test_delete_with_purge_still_purges_after_the_route_is_removed(world):
    world.settings.edge_enabled = True
    purged = []
    world.monkeypatch.setattr(provisioner, "_run_purge", lambda db, t: purged.append(t.tenant_name))
    tid = _new_tenant(world, status=TenantStatus.RUNNING)
    provisioner.delete_tenant(tid, purge=True)
    assert purged == ["hbssf"]
