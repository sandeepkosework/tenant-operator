"""The per-tenant `haproxy` and `godaddy` switches on the create request.

Both are OFF unless the request asks for them. The choice is stored on the tenant so provisioning, restore,
delete and POST /edge/sync all honour it. Asking for one the operator is not set up for is refused with a 409
before anything is created. A tenant that predates the choice (NULL) follows the operator-wide switches, so
the routes it already has are kept.

No real Git, SSH or GoDaddy: the SSH call is replaced by a fake edge host, the chart repository by a stub, and
the database is a throwaway SQLite file.
"""
import json
import types
import uuid
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models.tenant import Tenant, TenantStatus
from app.services import chart_versions, cluster_selector, edge_service, progress, provisioner
from app.services.git_service import GitServiceError

VIP = "192.168.85.207:443"
CLUSTER = types.SimpleNamespace(name="spoke-1", edge_upstream=VIP)


def _no_server(*a, **k):
    raise GitServiceError("could not list the chart repository's tags")


class Edge:
    """A fake edge host: route files and DNS records, answering the way edge-apply does."""

    def __init__(self):
        self.commands, self.routes, self.dns = [], {}, set()

    def __call__(self, command):
        self.commands.append(command)
        p = command.split()
        if p[0] == "list":
            return 0, json.dumps({"ok": True, "hosts": sorted(self.routes)}) + "\n"
        if p[0] == "dns-list":
            return 0, json.dumps({"ok": True, "hosts": sorted(self.dns)}) + "\n"
        if p[0] == "add":
            self.routes[p[1]] = p[2]
        elif p[0] == "remove":
            self.routes.pop(p[1], None)
        elif p[0] == "dns-add":
            self.dns.add(p[1])
        elif p[0] == "dns-remove":
            self.dns.discard(p[1])
        return 0, json.dumps({"ok": True, "host": p[1] if len(p) > 1 else "", "result": "added"}) + "\n"


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
    s = get_settings()
    monkeypatch.setattr(chart_versions.git_service, "list_remote_tags", _no_server)
    monkeypatch.setattr(s, "admin_password", "t")
    monkeypatch.setattr(s, "api_key", "k")
    monkeypatch.setattr(s, "edge_enabled", False)
    monkeypatch.setattr(s, "edge_dns_enabled", False)
    monkeypatch.setattr(s, "edge_required", False)
    monkeypatch.setattr(s, "edge_attempts", 1)
    monkeypatch.setattr(s, "edge_retry_delay_seconds", 0)
    edge = Edge()
    monkeypatch.setattr(edge_service, "_ssh_exec", edge)
    reg = [cluster_selector.ClusterInfo(name="spoke-1", context="c", argocd_cluster_server="x", max_tenants=1000,
                                        environments=["stage"], edge_upstream=VIP)]
    monkeypatch.setattr(cluster_selector, "load_cluster_registry", lambda: reg)
    yield types.SimpleNamespace(Session=Session, client=TestClient(app), headers={"X-API-Key": "k"}, edge=edge, settings=s,
                                monkeypatch=monkeypatch)
    app.dependency_overrides.clear()


def _post(env, name, **extra):
    body = {"tenantId": name, "displayName": name, "email": "a@b.co", "password": "changeme123",
            "domain": f"{name}.qraie.ai", **extra}
    with mock.patch("app.api.tenant.preflight.check_tenant_exists", return_value=([], [])), \
            mock.patch("app.api.tenant.provisioner.provision_tenant"):
        return env.client.post("/api/v1/tenant", json=body, headers=env.headers)


def _row(env, tid):
    db = env.Session()
    row = db.get(Tenant, uuid.UUID(str(tid)))          # the API hands the id back as a string
    db.expunge(row)
    db.close()
    return row


def _enable(env, route=True, dns=True):
    env.settings.edge_enabled = route
    env.settings.edge_dns_enabled = dns


# ---- the create request ------------------------------------------------------------------

def test_both_switches_are_off_when_the_request_does_not_mention_them(env):
    _enable(env)                                            # even with the operator fully set up
    r = _post(env, "plain")
    assert r.status_code == 202
    row = _row(env, r.json()["tenantId"])
    assert row.edge_route is False and row.edge_dns is False


def test_the_switches_are_stored_on_the_tenant(env):
    _enable(env)
    row = _row(env, _post(env, "both", haproxy=True, godaddy=True).json()["tenantId"])
    assert row.edge_route is True and row.edge_dns is True
    row = _row(env, _post(env, "routeonly", haproxy=True).json()["tenantId"])
    assert row.edge_route is True and row.edge_dns is False
    row = _row(env, _post(env, "dnsonly", godaddy=True).json()["tenantId"])
    assert row.edge_route is False and row.edge_dns is True


def test_asking_for_haproxy_when_edge_is_off_is_refused_and_nothing_is_created(env):
    r = _post(env, "wants-route", haproxy=True)
    assert r.status_code == 409 and "EDGE_ENABLED" in r.json()["detail"] and "Nothing was created" in r.json()["detail"]
    db = env.Session()
    assert db.query(Tenant).count() == 0
    db.close()


def test_asking_for_godaddy_needs_the_dns_switch_as_well(env):
    _enable(env, route=True, dns=False)
    r = _post(env, "wants-dns", godaddy=True)
    assert r.status_code == 409 and "EDGE_DNS_ENABLED" in r.json()["detail"]
    _enable(env, route=False, dns=True)                     # DNS rides on the edge login: both are needed
    assert _post(env, "wants-dns2", godaddy=True).status_code == 409


def test_a_request_that_asks_for_nothing_still_works_when_edge_is_off(env):
    assert _post(env, "quiet").status_code == 202
    assert _post(env, "quiet2", haproxy=False, godaddy=False).status_code == 202


def test_the_response_shows_the_choice_and_old_tenants_show_null(env):
    _enable(env)
    tid = _post(env, "shown", haproxy=True).json()["tenantId"]
    body = env.client.get(f"/api/v1/tenant/{tid}", headers=env.headers).json()
    assert body["haproxy"] is True and body["godaddy"] is False
    db = env.Session()
    old = Tenant(tenant_name="old", display_name="old", email="a@b.co", domain="old.qraie.ai", tier="standard", environment="stage",
                 app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0, database_size="10Gi",
                 status=TenantStatus.RUNNING, cluster="spoke-1", namespace="tenant-old", helm_chart_version="v0.1.8", disabled_services=[])
    db.add(old)
    db.commit()
    oid = old.id
    db.close()
    body = env.client.get(f"/api/v1/tenant/{oid}", headers=env.headers).json()
    assert body["haproxy"] is None and body["godaddy"] is None        # predates the choice


def test_create_options_say_what_this_operator_can_offer(env):
    assert env.client.get("/api/v1/meta/create-options", headers=env.headers).json()["edge"] == {"haproxy": False, "godaddy": False}
    _enable(env, route=True, dns=False)
    assert env.client.get("/api/v1/meta/create-options", headers=env.headers).json()["edge"] == {"haproxy": True, "godaddy": False}
    _enable(env)
    assert env.client.get("/api/v1/meta/create-options", headers=env.headers).json()["edge"] == {"haproxy": True, "godaddy": True}


# ---- an existing database is upgraded ----------------------------------------------------

def test_an_existing_database_gets_the_two_columns_and_old_rows_stay_null(tmp_path, monkeypatch):
    from sqlalchemy import inspect, text

    from app import database

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    db.add(Tenant(tenant_name="legacy", display_name="legacy", email="a@b.co", domain="legacy.qraie.ai", tier="standard",
                  environment="stage", app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0,
                  database_size="10Gi", status=TenantStatus.RUNNING, cluster="spoke-1", namespace="tenant-legacy",
                  helm_chart_version="v0.1.8", disabled_services=[]))
    db.commit()
    db.close()
    with engine.begin() as conn:                              # make it look like a database from before this change
        conn.execute(text("ALTER TABLE tenants DROP COLUMN edge_route"))
        conn.execute(text("ALTER TABLE tenants DROP COLUMN edge_dns"))
    assert {"edge_route", "edge_dns"}.isdisjoint(c["name"] for c in inspect(engine).get_columns("tenants"))

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "backfill_tenant_numbers", lambda: None)     # not what is under test; keep it off other databases
    database.ensure_schema()
    database.ensure_schema()                                                   # and it is safe to run twice

    assert {"edge_route", "edge_dns"} <= {c["name"] for c in inspect(engine).get_columns("tenants")}
    db = Session()
    row = db.query(Tenant).filter_by(tenant_name="legacy").one()
    assert row.edge_route is None and row.edge_dns is None                     # old tenants follow the operator-wide switches
    db.close()


# ---- the provisioner steps honour the stored choice --------------------------------------

@pytest.fixture
def steps(monkeypatch):
    rec = types.SimpleNamespace(events=[])
    monkeypatch.setattr(provisioner.progress, "record", lambda tid, step, state, msg="": rec.events.append((step, state, msg)))
    monkeypatch.setattr(provisioner, "_set_status", lambda *a, **k: None)
    return rec


def _t(route, dns):
    return types.SimpleNamespace(id="t-1", tenant_name="x", domain="x.qraie.ai", status=TenantStatus.RUNNING, edge_route=route, edge_dns=dns)


def test_a_tenant_that_did_not_ask_gets_neither_even_with_edge_fully_on(env, steps):
    _enable(env)
    t = _t(False, False)
    provisioner._publish_edge_route(None, t, CLUSTER)
    provisioner._publish_edge_dns(None, t)
    assert env.edge.commands == []
    assert [e[:2] for e in steps.events] == [("edge", "skip"), ("dns", "skip")]
    assert "haproxy=false" in steps.events[0][2] and "godaddy=false" in steps.events[1][2]


def test_a_tenant_that_asked_for_both_gets_both(env, steps):
    _enable(env)
    t = _t(True, True)
    provisioner._publish_edge_route(None, t, CLUSTER)
    provisioner._publish_edge_dns(None, t)
    assert env.edge.routes == {"x.qraie.ai": VIP} and env.edge.dns == {"x.qraie.ai"}


def test_the_two_are_independent(env, steps):
    _enable(env)
    provisioner._publish_edge_route(None, _t(True, False), CLUSTER)
    provisioner._publish_edge_dns(None, _t(True, False))
    assert env.edge.routes == {"x.qraie.ai": VIP} and env.edge.dns == set()
    env.edge.routes.clear()
    provisioner._publish_edge_route(None, _t(False, True), CLUSTER)
    provisioner._publish_edge_dns(None, _t(False, True))
    assert env.edge.routes == {} and env.edge.dns == {"x.qraie.ai"}


def test_a_tenant_that_predates_the_choice_follows_the_operator_wide_switches(env, steps):
    _enable(env)
    provisioner._publish_edge_route(None, _t(None, None), CLUSTER)
    provisioner._publish_edge_dns(None, _t(None, None))
    assert env.edge.routes == {"x.qraie.ai": VIP} and env.edge.dns == {"x.qraie.ai"}


def test_delete_leaves_alone_what_was_never_made_and_removes_what_was(env, steps):
    _enable(env)
    provisioner._remove_edge_route(_t(False, False))
    provisioner._remove_edge_dns(_t(False, False))
    assert env.edge.commands == []                           # no pointless SSH for a tenant that opted out
    provisioner._remove_edge_route(_t(True, True))
    provisioner._remove_edge_dns(_t(True, True))
    assert env.edge.commands == ["remove x.qraie.ai", "dns-remove x.qraie.ai"]


# ---- repair (POST /edge/sync) honours the choice ----------------------------------------

def _make(env, name, route, dns, status=TenantStatus.RUNNING):
    db = env.Session()
    db.add(Tenant(tenant_name=name, display_name=name, email="a@b.co", domain=f"{name}.qraie.ai", tier="standard", environment="stage",
                  app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0, database_size="10Gi", status=status,
                  cluster="spoke-1", namespace=f"tenant-{name}", helm_chart_version="v0.1.8", disabled_services=[],
                  edge_route=route, edge_dns=dns))
    db.commit()
    db.close()


def test_sync_does_not_add_what_a_tenant_declined(env):
    _enable(env)
    _make(env, "optedout", False, False)
    _make(env, "routed", True, False)
    _make(env, "dnsd", False, True)
    _make(env, "legacy", None, None)
    r = env.client.post("/api/v1/edge/sync", headers=env.headers).json()
    assert sorted(r["added"]) == ["legacy.qraie.ai", "routed.qraie.ai"]
    assert sorted(r["dnsAdded"]) == ["dnsd.qraie.ai", "legacy.qraie.ai"]
    assert "optedout.qraie.ai" not in env.edge.routes and "optedout.qraie.ai" not in env.edge.dns


def test_the_report_does_not_call_a_declined_route_missing(env):
    _enable(env)
    _make(env, "optedout", False, False)
    body = env.client.get("/api/v1/edge/routes", headers=env.headers).json()
    assert body["missing"] == [] and body["dns"]["missing"] == []


def test_prune_removes_a_declined_tenants_leftover_file_but_keeps_legacy_ones(env):
    _enable(env)
    _make(env, "optedout", False, False)
    _make(env, "legacy", None, None)
    env.edge.routes.update({"optedout.qraie.ai": VIP, "legacy.qraie.ai": VIP})      # e.g. made by hand, or before this switch
    r = env.client.post("/api/v1/edge/sync?prune=true", headers=env.headers).json()
    assert r["removed"] == ["optedout.qraie.ai"] and "legacy.qraie.ai" in env.edge.routes


# ---- the web form -----------------------------------------------------------------------

def test_the_form_only_offers_what_is_available_and_sends_false_otherwise():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "app" / "static" / "ui.html").read_text(encoding="utf-8")
    assert 'id="ntHaproxy"' in html and 'id="ntGodaddy"' in html
    assert 'type="checkbox" id="ntHaproxy" checked' not in html and 'id="ntGodaddy" checked' not in html     # unticked by default
    assert "body.haproxy = !$(\"ntHaproxyRow\").hidden && $(\"ntHaproxy\").checked" in html
    assert "#ntEdge label:not([hidden])" in html            # an inline display would override `hidden`
