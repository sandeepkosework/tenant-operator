"""tenant.loginId in a tenant's values file: set for a "<name>-bridgestg" tenant, absent otherwise.

Pure functions only (no Git, Vault or cluster).
"""
import pytest
import yaml

from app.services import helm_values


def _render(tenant_name):
    return yaml.safe_load(helm_values.render_qraie_bridge_values_yaml(
        tenant_name=tenant_name, tenant_slug=tenant_name, environment="stage",
        namespace=f"tenant-{tenant_name}", domain=f"{tenant_name}.qraie.ai",
        argocd_cluster_server="https://k", chart_version="v0.1.9",
    ))


@pytest.mark.parametrize("name,login", [("hbss-bridgestg", "hbss"), ("hbss-bridge", "hbss")])
def test_new_style_tenant_gets_its_login_id(name, login):
    tenant = _render(name)["tenant"]
    assert tenant["loginId"] == login
    assert tenant["name"] == name          # the full name still names everything else


@pytest.mark.parametrize("name", ["hbssf", "acme-corp"])
def test_other_tenants_files_are_unchanged(name):
    assert "loginId" not in _render(name)["tenant"]


@pytest.mark.parametrize("version, expected", [("v0.1.8", False), ("v0.1.9", True), ("v0.1.10", True),
                                               ("v0.2.0", True), (None, False)])
def test_which_releases_read_the_login_id(version, expected):
    assert helm_values.supports_login_id(version, "v0.1.9") is expected
