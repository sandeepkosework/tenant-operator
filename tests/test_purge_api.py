"""API-level tests for DELETE /tenant/{id}?purge=... and POST /tenant/{id}/purge.

The database session and the background work are faked: these check the status
codes, the query parameters, and that a refused purge never starts a delete.
"""
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tenant as tenant_api
from app.database import get_db
from app.models.tenant import TenantStatus
from app.services import provisioner, purge_service

TID = uuid.uuid4()


def _tenant(status=TenantStatus.RUNNING, environment="stage"):
    return types.SimpleNamespace(
        id=TID, tenant_name="hbss-036", slug="hbss-036", status=status, environment=environment,
    )


class _FakeSession:
    def __init__(self, tenant):
        self._tenant = tenant

    def get(self, model, key):
        return self._tenant if key == TID else None


@pytest.fixture
def calls(monkeypatch):
    log = {"delete": [], "purge": []}
    monkeypatch.setattr(provisioner, "delete_tenant", lambda tid, purge=False: log["delete"].append((tid, purge)))
    monkeypatch.setattr(provisioner, "purge_tenant", lambda tid: log["purge"].append(tid))
    monkeypatch.setattr(purge_service.settings, "purge_enabled", True)
    monkeypatch.setattr(purge_service.settings, "purge_allowed_environments", ["stage"])
    return log


def _client(tenant):
    app = FastAPI()
    app.include_router(tenant_api.router)
    app.dependency_overrides[get_db] = lambda: _FakeSession(tenant)
    return TestClient(app)


def test_plain_delete_is_unchanged(calls):
    r = _client(_tenant(status=TenantStatus.ARCHIVED)).delete(f"/tenant/{TID}")
    assert r.status_code == 202
    assert r.json()["purge"] is False
    assert calls["delete"] == [(TID, False)]


def test_delete_with_purge_starts_a_purging_delete(calls):
    r = _client(_tenant(status=TenantStatus.ARCHIVED)).delete(f"/tenant/{TID}?purge=true&confirm=hbss-036")
    assert r.status_code == 202
    assert r.json()["purge"] is True
    assert calls["delete"] == [(TID, True)]


def test_purge_without_confirm_is_refused_and_nothing_is_deleted(calls):
    r = _client(_tenant(status=TenantStatus.ARCHIVED)).delete(f"/tenant/{TID}?purge=true")
    assert r.status_code == 400
    assert calls["delete"] == []


def test_purge_when_disabled_is_refused_and_nothing_is_deleted(calls, monkeypatch):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", False)
    r = _client(_tenant(status=TenantStatus.ARCHIVED)).delete(f"/tenant/{TID}?purge=true&confirm=hbss-036")
    assert r.status_code == 403
    assert calls["delete"] == []


def test_purge_for_a_disallowed_environment_is_refused(calls):
    r = _client(_tenant(status=TenantStatus.ARCHIVED, environment="prod")).delete(f"/tenant/{TID}?purge=true&confirm=hbss-036")
    assert r.status_code == 403
    assert calls["delete"] == []


def test_unknown_tenant_is_404(calls):
    assert _client(_tenant()).delete(f"/tenant/{uuid.uuid4()}?purge=true&confirm=x").status_code == 404
    assert _client(_tenant()).post(f"/tenant/{uuid.uuid4()}/purge?dry_run=true").status_code == 404


def test_post_purge_needs_a_deleted_tenant(calls, monkeypatch):
    monkeypatch.setattr(purge_service, "check_purgeable", purge_service.check_purgeable)
    r = _client(_tenant(status=TenantStatus.RUNNING)).post(f"/tenant/{TID}/purge?confirm=hbss-036")
    assert r.status_code == 409
    assert calls["purge"] == []


def test_post_purge_runs_in_the_background(calls, monkeypatch):
    monkeypatch.setattr(purge_service, "check_purgeable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "check_previewable", lambda db, t: None)
    r = _client(_tenant(status=TenantStatus.DELETED)).post(f"/tenant/{TID}/purge?confirm=hbss-036")
    assert r.status_code == 202
    assert r.json()["status"] == "PURGING"
    assert calls["purge"] == [TID]


def test_post_purge_needs_confirm(calls, monkeypatch):
    monkeypatch.setattr(purge_service, "check_purgeable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "check_previewable", lambda db, t: None)
    r = _client(_tenant(status=TenantStatus.DELETED)).post(f"/tenant/{TID}/purge")
    assert r.status_code == 400
    assert calls["purge"] == []


def test_dry_run_needs_no_confirm_and_deletes_nothing(calls, monkeypatch):
    monkeypatch.setattr(purge_service, "check_purgeable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "check_previewable", lambda db, t: None)
    seen = {}

    def fake_run(tenant, dry_run=False):
        seen["dry_run"] = dry_run
        return [{"step": "mongo databases", "status": "would_do", "detail": "x"}]

    monkeypatch.setattr(purge_service, "purge_tenant_data", fake_run)
    r = _client(_tenant(status=TenantStatus.DELETED)).post(f"/tenant/{TID}/purge?dry_run=true")
    assert r.status_code == 200
    body = r.json()
    assert body["dryRun"] is True and body["steps"][0]["status"] == "would_do"
    assert seen["dry_run"] is True
    assert calls["purge"] == [] and calls["delete"] == []


def test_dry_run_works_even_when_purging_is_disabled(calls, monkeypatch):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", False)
    monkeypatch.setattr(purge_service, "check_purgeable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "check_previewable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "purge_tenant_data", lambda tenant, dry_run=False: [])
    assert _client(_tenant(status=TenantStatus.DELETED)).post(f"/tenant/{TID}/purge?dry_run=true").status_code == 200


# ---- a preview before the delete ---------------------------------------------------------

def test_a_preview_works_for_an_archived_tenant_and_says_a_purge_would_be_allowed(calls, monkeypatch):
    monkeypatch.setattr(purge_service, "check_previewable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "purge_tenant_data", lambda tenant, dry_run=False: [])
    r = _client(_tenant(status=TenantStatus.ARCHIVED)).post(f"/tenant/{TID}/purge?dry_run=true")
    assert r.status_code == 200
    assert r.json()["purgeAllowed"] is True and r.json()["purgeBlockedReason"] is None


def test_a_preview_says_why_a_purge_would_be_refused(calls, monkeypatch):
    monkeypatch.setattr(purge_service.settings, "purge_enabled", False)
    monkeypatch.setattr(purge_service, "check_previewable", lambda db, t: None)
    monkeypatch.setattr(purge_service, "purge_tenant_data", lambda tenant, dry_run=False: [])
    body = _client(_tenant(status=TenantStatus.ARCHIVED)).post(f"/tenant/{TID}/purge?dry_run=true").json()
    assert body["purgeAllowed"] is False and "PURGE_ENABLED" in body["purgeBlockedReason"]


@pytest.mark.parametrize("status", [TenantStatus.RUNNING, TenantStatus.ARCHIVING, TenantStatus.DELETING])
def test_a_running_tenants_data_is_not_previewed(calls, status):
    r = _client(_tenant(status=status)).post(f"/tenant/{TID}/purge?dry_run=true")
    assert r.status_code == 409 and "ARCHIVED, FAILED or DELETED" in r.json()["detail"]


def test_a_real_purge_still_needs_the_tenant_to_be_deleted(calls):
    r = _client(_tenant(status=TenantStatus.ARCHIVED)).post(f"/tenant/{TID}/purge?confirm=hbss-036")
    assert r.status_code == 409 and calls["purge"] == []
