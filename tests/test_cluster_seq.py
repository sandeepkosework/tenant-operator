"""Each tenant's number on its cluster: given when the cluster is chosen, never reused, filled in for
the tenants that already exist, and shown as `tenantSeq` in the tenant-info file.

The database is a throwaway SQLite file per test.
"""
import types
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base
from app.models.tenant import Tenant, TenantStatus
from app.services import cluster_selector, tenant_info


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    yield session
    session.close()


def _tenant(db, name, cluster, seq=None, status=TenantStatus.RUNNING, minutes=0):
    t = Tenant(tenant_name=name, display_name=name, email="a@b.co", domain=f"{name}.qraie.ai", tier="standard",
               environment="stage", app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0,
               database_size="10Gi", status=status, cluster=cluster, namespace=f"tenant-{name}",
               cluster_seq=seq, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes))
    db.add(t)
    db.commit()
    return t


def _seqs(db):
    return {t.tenant_name: t.cluster_seq for t in db.query(Tenant).all()}


# ---- the next number ---------------------------------------------------------------------

def test_the_first_tenant_on_a_cluster_is_number_one(db):
    assert cluster_selector.next_cluster_seq(db, "spoke-1") == 1


def test_numbers_count_up_per_cluster(db):
    _tenant(db, "a", "spoke-1", seq=1)
    _tenant(db, "b", "spoke-1", seq=2)
    _tenant(db, "c", "spoke-2", seq=1)
    assert cluster_selector.next_cluster_seq(db, "spoke-1") == 3
    assert cluster_selector.next_cluster_seq(db, "spoke-2") == 2


def test_a_deleted_tenants_number_is_not_reused(db):
    _tenant(db, "a", "spoke-1", seq=1)
    _tenant(db, "b", "spoke-1", seq=2, status=TenantStatus.DELETED)
    assert cluster_selector.next_cluster_seq(db, "spoke-1") == 3


# ---- tenants that already exist ----------------------------------------------------------

def test_existing_tenants_are_numbered_in_creation_order(db):
    _tenant(db, "later", "spoke-1", minutes=20)
    _tenant(db, "first", "spoke-1", minutes=1)
    _tenant(db, "middle", "spoke-1", minutes=10)
    _tenant(db, "other", "spoke-2", minutes=5)
    assert database.backfill_cluster_seq() == 4
    db.expire_all()
    assert _seqs(db) == {"first": 1, "middle": 2, "later": 3, "other": 1}


def test_the_backfill_continues_after_numbers_already_given(db):
    _tenant(db, "old", "spoke-1", seq=5, minutes=1)
    _tenant(db, "new", "spoke-1", minutes=2)
    database.backfill_cluster_seq()
    db.expire_all()
    assert _seqs(db) == {"old": 5, "new": 6}


def test_the_backfill_changes_nothing_when_run_again(db):
    _tenant(db, "a", "spoke-1", minutes=1)
    _tenant(db, "b", "spoke-1", minutes=2)
    database.backfill_cluster_seq()
    assert database.backfill_cluster_seq() == 0
    db.expire_all()
    assert _seqs(db) == {"a": 1, "b": 2}


def test_a_tenant_not_placed_on_a_cluster_yet_gets_no_number(db):
    _tenant(db, "waiting", None)
    assert database.backfill_cluster_seq() == 0
    db.expire_all()
    assert _seqs(db) == {"waiting": None}


def test_an_old_database_gets_the_column_and_its_numbers_at_startup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, tenant_name VARCHAR(63), cluster VARCHAR(64), "
                          "created_at DATETIME)"))
        conn.execute(text("INSERT INTO tenants VALUES ('b', 'second', 'spoke-1', '2026-10-02')"))
        conn.execute(text("INSERT INTO tenants VALUES ('a', 'first', 'spoke-1', '2026-10-01')"))
    monkeypatch.setattr(database, "engine", engine)

    database.ensure_schema()
    database.ensure_schema()                                    # a second start changes nothing

    with engine.begin() as conn:
        rows = dict(conn.execute(text("SELECT tenant_name, cluster_seq FROM tenants")).all())
    assert rows == {"first": 1, "second": 2}


# ---- the tenant-info file ----------------------------------------------------------------

CLUSTER = types.SimpleNamespace(name="spoke-1", region="local", argocd_cluster_server="https://spoke.example:6443")


def _info(tenant):
    return yaml.safe_load(tenant_info.render_tenant_info_yaml(tenant, CLUSTER))


def test_the_info_file_shows_the_number_on_the_cluster(db):
    assert _info(_tenant(db, "hbsspocfresh", "spoke-1", seq=7))["tenantSeq"] == 7


def test_an_old_tenant_without_one_shows_its_original_number(db):
    t = _tenant(db, "legacy", "spoke-1")
    t.tenant_seq = 42
    assert _info(t)["tenantSeq"] == 42
