"""How a tenant name turns into the login ID, the Mongo database and the checks on it."""
import pytest

from app.services import tenant_ids as t


@pytest.mark.parametrize("name,login,db", [
    ("chetan-bridgestg", "chetan", "chetan-bridgestg"),
    ("chetan-bridge", "chetan", "chetan-bridge"),
    ("tenant-bridgestg", "tenant", "tenant-bridgestg"),
    ("hbssf", "hbssf", "hbssf-bridge"),                      # older tenant: unchanged
    ("acme-corp", "acme-corp", "acme-corp-bridge"),          # hyphenated but no suffix: unchanged
    ("my-co-bridgestg", "my-co-bridgestg", "my-co-bridgestg-bridge"),  # more than one hyphen is not new-style
])
def test_login_id_and_mongo_database(name, login, db):
    assert t.login_id(name) == login
    assert t.mongo_db_name(name) == db


def test_environment_suffix():
    assert t.environment_suffix("stage") == "bridgestg"
    assert t.environment_suffix("prod") == "bridge"
    assert t.environment_suffix("Stage") == "bridgestg"
    assert t.environment_suffix("dev") is None


@pytest.mark.parametrize("name,env", [
    ("hbssf", "stage"), ("hbssf", "prod"),                   # no hyphen: always allowed
    ("chetan-bridgestg", "stage"), ("chetan-bridge", "prod"),
    ("chetan-bridgestg", "dev"), ("chetan-bridge", "dev"),   # other environments take either suffix
])
def test_names_that_are_allowed(name, env):
    assert t.check_tenant_name(name, env) is None


def test_the_other_environments_suffix_is_refused_with_the_right_one_named():
    assert "-bridgestg" in t.check_tenant_name("chetan-bridge", "stage")
    assert "chetan-bridge'" in t.check_tenant_name("chetan-bridgestg", "prod")


@pytest.mark.parametrize("name", ["acme-corp", "my-co-bridgestg", "chetan-bridgex", "-bridgestg", "a-b-bridge"])
def test_other_hyphenated_names_are_refused(name):
    assert t.check_tenant_name(name, "stage")
    assert t.check_tenant_name(name, "prod")
