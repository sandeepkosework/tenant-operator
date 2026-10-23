"""Each tenant's number across all spokes: given when the cluster is chosen, never reused, filled in for
the tenants that already exist, and shown as `tenantSeq` in the tenant-info file.

The database is a throwaway SQLite file per test.
"""
import types
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
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


def _tenant(db, name, cluster, number=None, status=TenantStatus.RUNNING, minutes=0):
    t = Tenant(tenant_name=name, display_name=name, email="a@b.co", domain=f"{name}.qraie.ai", tier="standard",
               environment="stage", app_type="qraie-bridge", application="qraie-bridge", version="1.0.0", users=0,
               database_size="10Gi", status=status, cluster=cluster, namespace=f"tenant-{name}",
               tenant_number=number, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes))
    db.add(t)
    db.commit()
    return t


def _numbers(db):
    return {t.tenant_name: t.tenant_number for t in db.query(Tenant).all()}


# ---- the next number ---------------------------------------------------------------------

def test_the_first_tenant_is_number_one(db):
    assert cluster_selector.next_tenant_number(db) == 1


def test_numbers_carry_on_across_spokes(db):
    for i in range(1, 11):
        _tenant(db, f"on-spoke-1-{i}", "spoke-1", number=i)
    assert cluster_selector.next_tenant_number(db) == 11          # the first tenant on spoke-2 is 11
    _tenant(db, "first-on-spoke-2", "spoke-2", number=11)
    assert cluster_selector.next_tenant_number(db) == 12


def test_a_deleted_tenants_number_is_not_reused(db):
    _tenant(db, "a", "spoke-1", number=1)
    _tenant(db, "b", "spoke-2", number=2, status=TenantStatus.DELETED)
    assert cluster_selector.next_tenant_number(db) == 3


# ---- two tenants never share a number ----------------------------------------------------

def test_the_database_refuses_a_number_that_is_already_taken(db):
    _tenant(db, "a", "spoke-1", number=1)
    with pytest.raises(IntegrityError):
        _tenant(db, "b", "spoke-2", number=1)


def test_tenants_with_no_number_yet_do_not_clash(db):
    _tenant(db, "a", None)
    _tenant(db, "b", None)                                      # two NULLs are fine


def test_a_collision_is_retried_with_the_next_number(db, monkeypatch):
    """Two tenants placed at the same moment both read "highest = 4" and try for 5; the loser gets 6."""
    _tenant(db, "taken", "spoke-1", number=5)
    mine = _tenant(db, "mine", "spoke-2")
    reads = iter([5, 5, 6])                                     # a stale read twice, then the free one
    monkeypatch.setattr(cluster_selector, "next_tenant_number", lambda session: next(reads))
    assert cluster_selector.assign_tenant_number(db, mine) == 6
    db.expire_all()
    assert _numbers(db) == {"taken": 5, "mine": 6}


def test_it_gives_up_after_too_many_collisions(db, monkeypatch):
    _tenant(db, "taken", "spoke-1", number=5)
    mine = _tenant(db, "mine", "spoke-2")
    monkeypatch.setattr(cluster_selector, "next_tenant_number", lambda session: 5)
    with pytest.raises(RuntimeError):
        cluster_selector.assign_tenant_number(db, mine, attempts=3)


def test_assigning_gives_the_next_free_number(db):
    _tenant(db, "a", "spoke-1", number=1)
    mine = _tenant(db, "mine", "spoke-2")
    assert cluster_selector.assign_tenant_number(db, mine) == 2


def test_the_startup_adds_the_unique_index_to_an_old_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old2.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, tenant_name VARCHAR(63), cluster VARCHAR(64), "
                          "created_at DATETIME, tenant_number INTEGER)"))
        conn.execute(text("INSERT INTO tenants VALUES ('a', 'a', 'spoke-1', '2026-10-01', 1)"))
    monkeypatch.setattr(database, "engine", engine)
    database.ensure_schema()
    with engine.begin() as conn:
        cols = "(id, tenant_name, cluster, created_at, tenant_number)"
        conn.execute(text(f"INSERT INTO tenants {cols} VALUES ('b', 'b', 'spoke-2', '2026-10-02', 2)"))
        with pytest.raises(IntegrityError):
            conn.execute(text(f"INSERT INTO tenants {cols} VALUES ('c', 'c', 'spoke-2', '2026-10-03', 2)"))


# ---- tenants that already exist ----------------------------------------------------------

def test_existing_tenants_are_numbered_across_clusters_in_creation_order(db):
    _tenant(db, "d", "spoke-2", minutes=40)
    _tenant(db, "a", "spoke-1", minutes=1)
    _tenant(db, "c", "spoke-2", minutes=30)
    _tenant(db, "b", "spoke-1", minutes=10)
    assert database.backfill_tenant_numbers() == 4
    db.expire_all()
    assert _numbers(db) == {"a": 1, "b": 2, "c": 3, "d": 4}


def test_the_backfill_continues_after_numbers_already_given(db):
    _tenant(db, "old", "spoke-1", number=5, minutes=1)
    _tenant(db, "new", "spoke-2", minutes=2)
    database.backfill_tenant_numbers()
    db.expire_all()
    assert _numbers(db) == {"old": 5, "new": 6}


def test_the_backfill_changes_nothing_when_run_again(db):
    _tenant(db, "a", "spoke-1", minutes=1)
    _tenant(db, "b", "spoke-2", minutes=2)
    database.backfill_tenant_numbers()
    assert database.backfill_tenant_numbers() == 0
    db.expire_all()
    assert _numbers(db) == {"a": 1, "b": 2}


def test_a_tenant_not_placed_on_a_cluster_yet_gets_no_number(db):
    _tenant(db, "waiting", None)
    assert database.backfill_tenant_numbers() == 0
    db.expire_all()
    assert _numbers(db) == {"waiting": None}


def test_an_old_database_gets_the_column_and_its_numbers_at_startup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, tenant_name VARCHAR(63), cluster VARCHAR(64), "
                          "created_at DATETIME)"))
        conn.execute(text("INSERT INTO tenants VALUES ('b', 'second', 'spoke-2', '2026-10-02')"))
        conn.execute(text("INSERT INTO tenants VALUES ('a', 'first', 'spoke-1', '2026-10-01')"))
    monkeypatch.setattr(database, "engine", engine)

    database.ensure_schema()
    database.ensure_schema()                                    # a second start changes nothing

    with engine.begin() as conn:
        rows = dict(conn.execute(text("SELECT tenant_name, tenant_number FROM tenants")).all())
    assert rows == {"first": 1, "second": 2}


def test_the_per_cluster_column_is_renamed_and_renumbered(tmp_path, monkeypatch):
    """A database that already ran the first version (cluster_seq, numbered per cluster)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'v1.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, tenant_name VARCHAR(63), cluster VARCHAR(64), "
                          "created_at DATETIME, cluster_seq INTEGER)"))
        conn.execute(text("INSERT INTO tenants VALUES ('a', 'a', 'spoke-1', '2026-10-01', 1)"))
        conn.execute(text("INSERT INTO tenants VALUES ('b', 'b', 'spoke-1', '2026-10-02', 2)"))
        conn.execute(text("INSERT INTO tenants VALUES ('c', 'c', 'spoke-2', '2026-10-03', 1)"))
    monkeypatch.setattr(database, "engine", engine)

    database.ensure_schema()

    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tenants)"))}
        rows = dict(conn.execute(text("SELECT tenant_name, tenant_number FROM tenants")).all())
    assert "cluster_seq" not in cols and "tenant_number" in cols
    assert rows == {"a": 1, "b": 2, "c": 3}


# ---- the tenant-info file ----------------------------------------------------------------

CLUSTER = types.SimpleNamespace(name="spoke-2", region="local", argocd_cluster_server="https://spoke.example:6443")


def _info(tenant):
    return yaml.safe_load(tenant_info.render_tenant_info_yaml(tenant, CLUSTER))


def test_the_info_file_shows_the_number_across_spokes(db):
    assert _info(_tenant(db, "hbsspocfresh", "spoke-2", number=11))["tenantSeq"] == 11


def test_an_old_tenant_without_one_shows_its_original_number(db):
    t = _tenant(db, "legacy", "spoke-1")
    t.tenant_seq = 42
    assert _info(t)["tenantSeq"] == 42
