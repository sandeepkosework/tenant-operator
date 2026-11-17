"""Edge HAProxy routes: one backend file per tenant on the edge host, made over SSH by `edge-apply`.

No real SSH, HAProxy or edge host: the one function that opens the SSH session (`_ssh_exec`) is
replaced by a fake that records the command it was given and answers like the real script does.
The database is a throwaway SQLite file per test.
"""
import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models.tenant import Tenant, TenantStatus
from app.services import cluster_selector, edge_service, progress, provisioner

VIP = "192.168.85.207:443"
CLUSTER = types.SimpleNamespace(name="spoke-1", edge_upstream=VIP)


class FakeEdge:
    """Stands in for the edge host: keeps the set of routes like conf.d/ does, answers like edge-apply."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.commands = []
        self.fail_connect = 0            # connection failures to raise before answering
        self.refuse = None               # an error message edge-apply refuses with
        self.dns = set()                 # the tenants' A records "at GoDaddy"
        self.dns_error = None            # make dns-list fail (say, GoDaddy refusing the key)

    def __call__(self, command):
        self.commands.append(command)
        if self.fail_connect > 0:
            self.fail_connect -= 1
            raise edge_service.EdgeError("cannot reach the edge host: timed out")
        parts = command.split()
        if parts[0] == "list":                       # listing is never refused, only changes are
            return 0, json.dumps({"ok": True, "hosts": sorted(self.routes)}) + "\n"
        if self.refuse:
            return 1, json.dumps({"ok": False, "error": self.refuse}) + "\n"
        if parts[0] == "add":
            host, up = parts[1], parts[2]
            result = "unchanged" if self.routes.get(host) == up else ("updated" if host in self.routes else "added")
            self.routes[host] = up
            return 0, json.dumps({"ok": True, "host": host, "result": result}) + "\n"
        if parts[0] == "remove":
            result = "removed" if self.routes.pop(parts[1], None) else "absent"
            return 0, json.dumps({"ok": True, "host": parts[1], "result": result}) + "\n"
        if parts[0] == "dns-list":
            if self.dns_error:
                return 1, json.dumps({"ok": False, "error": self.dns_error}) + "\n"
            return 0, json.dumps({"ok": True, "hosts": sorted(self.dns)}) + "\n"
        if parts[0] == "dns-add":
            result = "unchanged" if parts[1] in self.dns else "added"
            self.dns.add(parts[1])
            return 0, json.dumps({"ok": True, "host": parts[1], "result": result}) + "\n"
        if parts[0] == "dns-remove":
            result = "removed" if parts[1] in self.dns else "absent"
            self.dns.discard(parts[1])
            return 0, json.dumps({"ok": True, "host": parts[1], "result": result}) + "\n"
        return 1, json.dumps({"ok": False, "error": "unknown command"}) + "\n"


@pytest.fixture
def edge(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "edge_enabled", True)
    monkeypatch.setattr(s, "edge_required", False)
    monkeypatch.setattr(s, "edge_attempts", 3)
    monkeypatch.setattr(s, "edge_retry_delay_seconds", 0)
    monkeypatch.setattr(s, "edge_default_upstream", None)
    fake = FakeEdge()
    monkeypatch.setattr(edge_service, "_ssh_exec", fake)
    return fake


# ---- validation --------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["hbssf.qraie.ai", "acme-1.qraie.ai", "a.b.qraie.ai"])
def test_valid_hosts_are_accepted(host):
    assert edge_service.validate_host(host) == host


@pytest.mark.parametrize("host", ["", "hbssf", "HBSSF.qraie.ai ", "a b.qraie.ai", "a;id.qraie.ai", "-a.qraie.ai",
                                  "a..qraie.ai", "a/../b.qraie.ai", "x" * 300 + ".qraie.ai"])
def test_bad_hosts_are_refused_before_anything_is_sent(host, edge):
    # a stray uppercase or trailing space is normalised; everything else is refused
    try:
        clean = edge_service.validate_host(host)
    except edge_service.EdgeError:
        assert edge.commands == []
        return
    assert clean == host.strip().lower() and " " not in clean and ";" not in clean


@pytest.mark.parametrize("up", ["192.168.85.207:443", "ingress.spoke2.internal:443"])
def test_valid_upstreams(up):
    assert edge_service.validate_upstream(up) == up


@pytest.mark.parametrize("up", ["", "192.168.85.207", "192.168.85.207:0", "192.168.85.207:99999", "a b:443", "x;id:443"])
def test_bad_upstreams_are_refused(up):
    with pytest.raises(edge_service.EdgeError):
        edge_service.validate_upstream(up)


# ---- the spoke's upstream -----------------------------------------------------------------

def test_the_spokes_own_upstream_is_used(edge):
    assert edge_service.upstream_for(types.SimpleNamespace(name="spoke-2", edge_upstream="192.168.85.210:443")) == "192.168.85.210:443"


def test_the_default_upstream_covers_a_spoke_without_one(edge, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_default_upstream", VIP)
    assert edge_service.upstream_for(types.SimpleNamespace(name="spoke-1", edge_upstream=None)) == VIP


def test_a_spoke_with_no_upstream_and_no_default_is_an_error_not_a_guess(edge):
    with pytest.raises(edge_service.EdgeError, match="spoke-1"):
        edge_service.upstream_for(types.SimpleNamespace(name="spoke-1", edge_upstream=None))


def test_the_cluster_registry_reads_edge_upstream():
    info = cluster_selector.ClusterInfo(name="s", context="c", argocd_cluster_server="x", max_tenants=1,
                                        environments=["stage"], edge_upstream=VIP)
    assert info.edge_upstream == VIP
    assert cluster_selector.ClusterInfo(name="s", context="c", argocd_cluster_server="x", max_tenants=1,
                                        environments=["stage"]).edge_upstream is None


# ---- operations ---------------------------------------------------------------------------

def test_add_sends_exactly_the_forced_command(edge):
    assert edge_service.ensure_route("hbssf.qraie.ai", VIP) == "added"
    assert edge.commands == [f"add hbssf.qraie.ai {VIP}"]
    assert edge.routes == {"hbssf.qraie.ai": VIP}


def test_adding_twice_is_unchanged_and_a_new_upstream_is_an_update(edge):
    edge_service.ensure_route("hbssf.qraie.ai", VIP)
    assert edge_service.ensure_route("hbssf.qraie.ai", VIP) == "unchanged"
    assert edge_service.ensure_route("hbssf.qraie.ai", "192.168.85.210:443") == "updated"


def test_remove_and_removing_again(edge):
    edge_service.ensure_route("hbssf.qraie.ai", VIP)
    assert edge_service.remove_route("hbssf.qraie.ai") == "removed"
    assert edge_service.remove_route("hbssf.qraie.ai") == "absent"      # already gone is success


def test_list(edge):
    edge_service.ensure_route("b.qraie.ai", VIP)
    edge_service.ensure_route("a.qraie.ai", VIP)
    assert edge_service.list_routes() == ["a.qraie.ai", "b.qraie.ai"]


def test_a_refusal_is_an_error_and_is_not_retried(edge):
    edge.refuse = "upstream is not in allowed-upstreams"
    with pytest.raises(edge_service.EdgeError, match="allowed-upstreams"):
        edge_service.ensure_route("hbssf.qraie.ai", VIP)
    assert len(edge.commands) == 1


def test_a_connection_failure_is_retried_then_succeeds(edge):
    edge.fail_connect = 2
    assert edge_service.ensure_route("hbssf.qraie.ai", VIP) == "added"
    assert len(edge.commands) == 3


def test_it_gives_up_after_the_attempts(edge):
    edge.fail_connect = 99
    with pytest.raises(edge_service.EdgeError, match="unreachable after 3 attempts"):
        edge_service.ensure_route("hbssf.qraie.ai", VIP)
    assert len(edge.commands) == 3


def test_an_answer_that_is_not_json_is_an_error(monkeypatch, edge):
    monkeypatch.setattr(edge_service, "_ssh_exec", lambda c: (0, "Welcome to Ubuntu\n"))
    with pytest.raises(edge_service.EdgeError, match="unexpected answer"):
        edge_service.list_routes()


def test_it_refuses_to_connect_without_a_host_key_to_verify(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "edge_ssh_host", "192.168.85.205")
    monkeypatch.setattr(s, "edge_ssh_host_key", None)
    monkeypatch.setattr(s, "edge_ssh_key_path", None)
    with pytest.raises(edge_service.EdgeError, match="EDGE_SSH_HOST_KEY"):
        edge_service._ssh_exec("list")


def _fake_ssh(monkeypatch, answer=b'{"ok": true, "hosts": []}\n', connect_error=None):
    """Replaces paramiko's SSHClient with one that records how it was used, and sets a real host key."""
    import paramiko

    key = paramiko.RSAKey.generate(2048)
    s = get_settings()
    monkeypatch.setattr(s, "edge_ssh_host", "192.168.85.205")
    monkeypatch.setattr(s, "edge_ssh_port", 22)
    monkeypatch.setattr(s, "edge_ssh_user", "edge")
    monkeypatch.setattr(s, "edge_ssh_host_key", f"ssh-rsa {key.get_base64()}")
    monkeypatch.setattr(s, "edge_ssh_key_path", None)
    monkeypatch.setattr(s, "edge_ssh_password", None)
    monkeypatch.setattr(s, "edge_ssh_command", "")
    rec = types.SimpleNamespace(connect=None, command=None, policy=None, hostkeys=[], closed=False)

    class Chan:
        def recv_exit_status(self):
            return 0

    class Out:
        channel = Chan()

        def read(self):
            return answer

    class Client:
        def get_host_keys(self):
            return types.SimpleNamespace(add=lambda n, t, k: rec.hostkeys.append((n, t)))

        def set_missing_host_key_policy(self, p):
            rec.policy = p

        def connect(self, host, **kw):
            rec.connect = (host, kw)
            if connect_error:
                raise connect_error

        def exec_command(self, cmd, timeout=None):
            rec.command = cmd
            return None, Out(), None

        def close(self):
            rec.closed = True

    monkeypatch.setattr(paramiko, "SSHClient", Client)
    return rec


def test_password_login_sends_the_password_and_still_pins_the_host_key(monkeypatch):
    import paramiko

    rec = _fake_ssh(monkeypatch)
    monkeypatch.setattr(get_settings(), "edge_ssh_password", "s3cret-pw")
    assert edge_service._ssh_exec("list") == (0, '{"ok": true, "hosts": []}\n')
    host, kw = rec.connect
    assert host == "192.168.85.205" and kw["username"] == "edge"
    assert kw["password"] == "s3cret-pw" and kw["pkey"] is None
    assert kw["look_for_keys"] is False and kw["allow_agent"] is False        # no ambient keys are tried
    assert isinstance(rec.policy, paramiko.RejectPolicy)                      # an unknown or changed host key is refused
    assert rec.hostkeys == [("192.168.85.205", "ssh-rsa")]
    assert rec.command == "list" and rec.closed


def test_a_normal_login_runs_edge_apply_through_sudo_with_one_quoted_argument(monkeypatch):
    rec = _fake_ssh(monkeypatch)
    s = get_settings()
    monkeypatch.setattr(s, "edge_ssh_password", "s3cret-pw")
    monkeypatch.setattr(s, "edge_ssh_command", "sudo -n /opt/edge/bin/edge-apply --run")
    edge_service._ssh_exec("add hbssf.qraie.ai 192.168.85.207:443")
    assert rec.command == "sudo -n /opt/edge/bin/edge-apply --run 'add hbssf.qraie.ai 192.168.85.207:443'"


def test_the_prefix_cannot_be_escaped_by_the_command(monkeypatch):
    rec = _fake_ssh(monkeypatch)
    s = get_settings()
    monkeypatch.setattr(s, "edge_ssh_password", "x")
    monkeypatch.setattr(s, "edge_ssh_command", "sudo -n /opt/edge/bin/edge-apply --run")
    edge_service._ssh_exec("add a.qraie.ai 1.2.3.4:443'; id; '")               # never produced by the validators, but quoted anyway
    assert rec.command.startswith("sudo -n /opt/edge/bin/edge-apply --run ") and "; id;" not in rec.command.split("--run ", 1)[0]
    assert "'\"'\"'" in rec.command                                            # the quote is escaped, not closed


def test_it_needs_a_key_or_a_password(monkeypatch):
    _fake_ssh(monkeypatch)
    with pytest.raises(edge_service.EdgeError, match="EDGE_SSH_PASSWORD"):
        edge_service._ssh_exec("list")


def test_a_wrong_password_is_reported_without_revealing_it(monkeypatch):
    import paramiko

    _fake_ssh(monkeypatch, connect_error=paramiko.AuthenticationException("Authentication failed."))
    monkeypatch.setattr(get_settings(), "edge_ssh_password", "s3cret-pw")
    with pytest.raises(edge_service.EdgeError) as e:
        edge_service._ssh_exec("list")
    assert "Authentication failed" in str(e.value) and "s3cret-pw" not in str(e.value)


def test_a_malformed_host_key_is_refused():
    with pytest.raises(edge_service.EdgeError):
        edge_service._load_host_key("not a key")


# ---- provisioner steps --------------------------------------------------------------------

@pytest.fixture
def steps(monkeypatch):
    """Records progress events and status changes instead of writing them to a database."""
    rec = types.SimpleNamespace(events=[], statuses=[])
    monkeypatch.setattr(provisioner.progress, "record", lambda tid, step, state, msg="": rec.events.append((step, state, msg)))
    monkeypatch.setattr(provisioner, "_set_status", lambda db, t, status, error=None: rec.statuses.append((status, error)))
    return rec


def _tenant(domain="hbssf.qraie.ai"):
    return types.SimpleNamespace(id="t-1", tenant_name="hbssf", domain=domain, status=TenantStatus.RUNNING)


def test_a_running_tenant_gets_its_route(edge, steps):
    provisioner._publish_edge_route(None, _tenant(), CLUSTER)
    assert edge.routes == {"hbssf.qraie.ai": VIP}
    assert steps.events[-1][:2] == ("edge", "done") and steps.statuses == []


def test_a_second_spokes_tenant_gets_that_spokes_address(edge, steps):
    provisioner._publish_edge_route(None, _tenant("acme.qraie.ai"), types.SimpleNamespace(name="spoke-2", edge_upstream="192.168.85.210:443"))
    assert edge.routes == {"acme.qraie.ai": "192.168.85.210:443"}


def test_nothing_is_done_when_edge_is_off(edge, steps, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_enabled", False)
    provisioner._publish_edge_route(None, _tenant(), CLUSTER)
    assert edge.commands == [] and steps.events[-1][:2] == ("edge", "skip")


def test_a_failed_route_leaves_the_tenant_running_by_default(edge, steps):
    edge.refuse = "upstream is not in allowed-upstreams"
    provisioner._publish_edge_route(None, _tenant(), CLUSTER)
    assert steps.statuses == [] and steps.events[-1][:2] == ("edge", "warn")


def test_a_failed_route_fails_the_tenant_when_required(edge, steps, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_required", True)
    edge.refuse = "upstream is not in allowed-upstreams"
    provisioner._publish_edge_route(None, _tenant(), CLUSTER)
    assert steps.statuses and steps.statuses[0][0] == TenantStatus.FAILED and "edge route failed" in steps.statuses[0][1]


def test_a_spoke_without_an_upstream_is_reported_not_guessed(edge, steps):
    provisioner._publish_edge_route(None, _tenant(), types.SimpleNamespace(name="spoke-1", edge_upstream=None))
    assert edge.commands == [] and steps.events[-1][:2] == ("edge", "warn")


def test_deleting_a_tenant_removes_its_route(edge, steps):
    edge.routes["hbssf.qraie.ai"] = VIP
    provisioner._remove_edge_route(_tenant())
    assert edge.routes == {}


def test_a_failed_removal_never_fails_the_delete(edge, steps):
    edge.fail_connect = 99
    provisioner._remove_edge_route(_tenant())          # must not raise
    assert steps.events[-1][:2] == ("edge", "warn")


def test_the_new_step_is_in_the_creation_steps_before_erep():
    keys = [k for k, _ in progress.CREATE_STEPS]
    assert keys.index("deploy") < keys.index("edge") < keys.index("erep")


# ---- API ----------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch, edge):
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
    monkeypatch.setattr(s, "admin_password", "t")
    monkeypatch.setattr(s, "api_key", "k")
    reg = [cluster_selector.ClusterInfo(name="spoke-1", context="c", argocd_cluster_server="x", max_tenants=1000,
                                        environments=["stage"], edge_upstream=VIP),
           cluster_selector.ClusterInfo(name="spoke-2", context="c2", argocd_cluster_server="y", max_tenants=1000,
                                        environments=["stage"], edge_upstream="192.168.85.210:443")]
    monkeypatch.setattr(cluster_selector, "load_cluster_registry", lambda: reg)
    yield types.SimpleNamespace(Session=Session, client=TestClient(app), headers={"X-API-Key": "k"}, edge=edge)
    app.dependency_overrides.clear()


def _make(env, name, status=TenantStatus.RUNNING, cluster="spoke-1"):
    db = env.Session()
    t = Tenant(tenant_name=name, display_name=name, email="a@b.co", domain=f"{name}.qraie.ai", tier="standard",
               environment="stage", app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0,
               database_size="10Gi", status=status, cluster=cluster, namespace=f"tenant-{name}",
               helm_chart_version="v0.1.8", disabled_services=[])
    db.add(t)
    db.commit()
    db.close()


def test_routes_shows_missing_and_orphans(env):
    _make(env, "hbssf")
    _make(env, "hbssg")
    _make(env, "gone", status=TenantStatus.DELETED)           # a deleted tenant should have no route
    env.edge.routes.update({"hbssf.qraie.ai": VIP, "gone.qraie.ai": VIP, "handmade.qraie.ai": VIP})
    r = env.client.get("/api/v1/edge/routes", headers=env.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["missing"] == ["hbssg.qraie.ai"]
    assert body["orphans"] == ["gone.qraie.ai", "handmade.qraie.ai"]


def test_sync_adds_the_missing_routes_with_each_tenants_own_spoke(env):
    _make(env, "hbssf", cluster="spoke-1")
    _make(env, "acme", cluster="spoke-2")
    r = env.client.post("/api/v1/edge/sync", headers=env.headers)
    assert r.status_code == 200 and sorted(r.json()["added"]) == ["acme.qraie.ai", "hbssf.qraie.ai"]
    assert env.edge.routes == {"hbssf.qraie.ai": VIP, "acme.qraie.ai": "192.168.85.210:443"}


def test_sync_does_not_prune_unless_asked(env):
    _make(env, "hbssf")
    env.edge.routes.update({"hbssf.qraie.ai": VIP, "old.qraie.ai": VIP})
    r = env.client.post("/api/v1/edge/sync", headers=env.headers)
    assert r.json()["removed"] == [] and "old.qraie.ai" in env.edge.routes
    r = env.client.post("/api/v1/edge/sync?prune=true", headers=env.headers)
    assert r.json()["removed"] == ["old.qraie.ai"] and "old.qraie.ai" not in env.edge.routes


def test_sync_with_rewrite_picks_up_a_changed_upstream(env):
    _make(env, "acme", cluster="spoke-2")
    env.edge.routes["acme.qraie.ai"] = VIP                                  # moved spokes since the file was written
    r = env.client.post("/api/v1/edge/sync?rewrite=true", headers=env.headers)
    assert r.json()["updated"] == ["acme.qraie.ai"]
    assert env.edge.routes["acme.qraie.ai"] == "192.168.85.210:443"


def test_sync_reports_a_failure_per_host_and_carries_on(env):
    _make(env, "hbssf")
    env.edge.refuse = "upstream is not in allowed-upstreams"
    r = env.client.post("/api/v1/edge/sync", headers=env.headers)
    assert r.status_code == 200 and r.json()["failed"][0]["host"] == "hbssf.qraie.ai"


# ---- DNS records --------------------------------------------------------------------------

@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_dns_enabled", True)


def test_dns_needs_both_switches(edge, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "edge_dns_enabled", False)
    assert not edge_service.dns_enabled()
    monkeypatch.setattr(s, "edge_dns_enabled", True)
    monkeypatch.setattr(s, "edge_enabled", False)
    assert not edge_service.dns_enabled()                        # DNS rides on the edge SSH login
    monkeypatch.setattr(s, "edge_enabled", True)
    assert edge_service.dns_enabled()


def test_dns_commands_are_exactly_the_forced_commands(edge, dns):
    assert edge_service.ensure_dns("hbssf.qraie.ai") == "added"
    assert edge_service.ensure_dns("hbssf.qraie.ai") == "unchanged"
    assert edge_service.list_dns() == ["hbssf.qraie.ai"]
    assert edge_service.remove_dns("hbssf.qraie.ai") == "removed"
    assert edge_service.remove_dns("hbssf.qraie.ai") == "absent"
    assert edge.commands == ["dns-add hbssf.qraie.ai", "dns-add hbssf.qraie.ai", "dns-list",
                             "dns-remove hbssf.qraie.ai", "dns-remove hbssf.qraie.ai"]


@pytest.mark.parametrize("host", ["", "a;id.qraie.ai", "UPPER..qraie.ai"])
def test_dns_refuses_a_bad_host_before_sending_anything(edge, dns, host):
    with pytest.raises(edge_service.EdgeError):
        edge_service.ensure_dns(host)
    assert edge.commands == []


def test_dns_provisioner_step_creates_the_record(edge, dns, steps):
    provisioner._publish_edge_dns(None, _tenant())
    assert edge.dns == {"hbssf.qraie.ai"} and steps.events[-1][:2] == ("dns", "done") and steps.statuses == []


def test_dns_step_is_skipped_when_off(edge, steps):
    provisioner._publish_edge_dns(None, _tenant())
    assert edge.commands == [] and steps.events[-1][:2] == ("dns", "skip")


@pytest.mark.parametrize("boom", [edge_service.EdgeError("godaddy refused the API key (HTTP 403)"), RuntimeError("odd")])
def test_a_dns_problem_leaves_the_tenant_running_by_default(edge, dns, steps, monkeypatch, boom):
    def raiser(command):
        raise boom
    monkeypatch.setattr(edge_service, "_ssh_exec", raiser)
    provisioner._publish_edge_dns(None, _tenant())
    assert steps.statuses == [] and steps.events[-1][:2] == ("dns", "warn")


def test_a_dns_problem_fails_the_tenant_when_required(edge, dns, steps, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_required", True)
    edge.refuse = "godaddy refused the API key (HTTP 403)"
    provisioner._publish_edge_dns(None, _tenant())
    assert steps.statuses and steps.statuses[0][0] == TenantStatus.FAILED and "DNS record failed" in steps.statuses[0][1]


def test_deleting_a_tenant_removes_its_dns_record_and_never_fails_over_it(edge, dns, steps):
    edge.dns.add("hbssf.qraie.ai")
    provisioner._remove_edge_dns(_tenant())
    assert edge.dns == set()
    edge.fail_connect = 99
    provisioner._remove_edge_dns(_tenant())                     # must not raise
    assert steps.events[-1][:2] == ("dns", "warn")


def test_the_dns_step_follows_the_route_step_and_precedes_the_erep():
    keys = [k for k, _ in progress.CREATE_STEPS]
    assert keys.index("edge") < keys.index("dns") < keys.index("erep")


def test_routes_report_has_no_dns_section_when_dns_is_off(env):
    assert env.client.get("/api/v1/edge/routes", headers=env.headers).json()["dns"] is None


def test_routes_report_shows_dns_missing_and_orphans(env, dns):
    _make(env, "hbssf")
    _make(env, "hbssg")
    env.edge.routes.update({"hbssf.qraie.ai": VIP, "hbssg.qraie.ai": VIP})
    env.edge.dns.update({"hbssf.qraie.ai", "old.qraie.ai"})
    body = env.client.get("/api/v1/edge/routes", headers=env.headers).json()
    assert body["dns"] == {"missing": ["hbssg.qraie.ai"], "orphans": ["old.qraie.ai"], "error": None}


def test_a_dns_error_does_not_hide_the_route_report(env, dns):
    _make(env, "hbssf")
    env.edge.dns_error = "godaddy refused the API key (HTTP 403: wrong key, or this account has no API access)"
    r = env.client.get("/api/v1/edge/routes", headers=env.headers)
    assert r.status_code == 200 and r.json()["missing"] == ["hbssf.qraie.ai"]
    assert "refused the API key" in r.json()["dns"]["error"]


def test_sync_adds_missing_dns_records_and_leaves_orphans_alone(env, dns):
    _make(env, "hbssf")
    env.edge.dns.add("old.qraie.ai")
    r = env.client.post("/api/v1/edge/sync", headers=env.headers).json()
    assert r["dnsAdded"] == ["hbssf.qraie.ai"] and r["dnsRemoved"] == []
    assert env.edge.dns == {"hbssf.qraie.ai", "old.qraie.ai"}


def test_orphan_dns_records_go_only_with_the_separate_flag(env, dns):
    _make(env, "hbssf")
    env.edge.routes["old.qraie.ai"] = VIP
    env.edge.dns.update({"hbssf.qraie.ai", "old.qraie.ai"})
    r = env.client.post("/api/v1/edge/sync?prune=true", headers=env.headers).json()
    assert r["removed"] == ["old.qraie.ai"] and r["dnsRemoved"] == []          # prune is for routes only
    assert "old.qraie.ai" in env.edge.dns
    r = env.client.post("/api/v1/edge/sync?pruneDns=true", headers=env.headers).json()
    assert r["dnsRemoved"] == ["old.qraie.ai"] and env.edge.dns == {"hbssf.qraie.ai"}


def test_the_edge_api_is_off_when_edge_is_disabled(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_enabled", False)
    assert env.client.get("/api/v1/edge/routes", headers=env.headers).status_code == 409
    assert env.client.post("/api/v1/edge/sync", headers=env.headers).status_code == 409


def test_the_edge_api_needs_credentials(env):
    assert env.client.get("/api/v1/edge/routes").status_code in (401, 503)


def test_an_unreachable_edge_host_is_a_502(env):
    env.edge.fail_connect = 99
    assert env.client.get("/api/v1/edge/routes", headers=env.headers).status_code == 502
