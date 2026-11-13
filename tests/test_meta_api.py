"""GET /meta/create-options: what the create-tenant form offers.

The chart repository is faked, so no Git is touched.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import meta as meta_api
from app.services import chart_versions
from app.services.helm_values import QRAIE_BRIDGE_SERVICE_NAMES


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(meta_api.router)
    return TestClient(app)


def test_lists_every_service_sorted_with_domain_and_versions(client, monkeypatch):
    monkeypatch.setattr(chart_versions, "available", lambda: ["v0.1.0", "v0.1.6"])
    body = client.get("/meta/create-options").json()
    assert body["services"] == sorted(QRAIE_BRIDGE_SERVICE_NAMES)
    assert body["baseDomain"] == meta_api.settings.bridge_base_domain
    assert body["chartVersions"] == ["v0.1.0", "v0.1.6"]
    assert body["defaultChartVersion"] == meta_api.settings.helm_chart_version


def test_chart_versions_is_null_when_the_repository_cannot_be_read(client, monkeypatch):
    monkeypatch.setattr(chart_versions, "available", lambda: None)
    assert client.get("/meta/create-options").json()["chartVersions"] is None
