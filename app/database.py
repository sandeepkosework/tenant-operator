from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and guarantees it's closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Bring an existing database up to the current models. create_all() only creates tables that are
    missing, so a column or enum value added later has to be added here. Every step is idempotent and
    a failure is logged, not raised: the operator should still start."""
    import logging

    from sqlalchemy import inspect, text

    log = logging.getLogger("tenant-operator.database")
    from app.models.tenant import Tenant, TenantStatus

    table = Tenant.__tablename__
    try:
        # The number was first kept per cluster, as cluster_seq; keep the column and its values.
        existing = {c["name"] for c in inspect(engine).get_columns(table)}
        if "cluster_seq" in existing and "tenant_number" not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN cluster_seq TO tenant_number"))
            log.info("renamed %s.cluster_seq to tenant_number", table)
    except Exception:  # noqa: BLE001
        log.exception("could not rename %s.cluster_seq to tenant_number", table)

    timestamp = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
    for column, column_type, why in (
        ("archived_at", timestamp, "archive/restore will not work"),
        ("tenant_number", "INTEGER", "tenants will have no number"),
    ):
        try:
            if column not in {c["name"] for c in inspect(engine).get_columns(table)}:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
                log.info("added column %s.%s", table, column)
        except Exception:  # noqa: BLE001
            log.exception("could not add %s.%s -- %s until it exists", table, column, why)

    try:
        backfill_tenant_numbers()
    except Exception:  # noqa: BLE001
        log.exception("could not number the existing tenants")

    if engine.dialect.name != "postgresql":
        return      # elsewhere the status column is plain text; nothing to add
    type_name = Tenant.__table__.c.status.type.name
    try:
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older Postgres versions.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for status in TenantStatus:
                conn.execute(text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{status.name}'"))
    except Exception:  # noqa: BLE001
        log.exception("could not add the tenant status values to the %s enum", type_name)


def backfill_tenant_numbers() -> int:
    """Number the tenants that have a cluster, across all spokes, in creation order. Returns how many rows
    were numbered or renumbered; 0 when there is nothing to do, so it is safe to run at every start.

    - Numbers that were given per cluster (the first version of this) repeat across clusters, so the
      whole set is renumbered once, from 1: the first tenant is 1 and the first one on a second spoke
      carries on from where the first spoke stopped.
    - Otherwise only the tenants with no number yet get one, after the highest already used."""
    from sqlalchemy import text

    numbered = 0
    with engine.begin() as conn:
        duplicates = conn.execute(text(
            "SELECT COUNT(tenant_number) - COUNT(DISTINCT tenant_number) FROM tenants")).scalar() or 0
        if duplicates:
            ids = [row[0] for row in conn.execute(text(
                "SELECT id FROM tenants WHERE cluster IS NOT NULL ORDER BY created_at, id"))]
            start = 0
        else:
            start = conn.execute(text("SELECT COALESCE(MAX(tenant_number), 0) FROM tenants")).scalar() or 0
            ids = [row[0] for row in conn.execute(text(
                "SELECT id FROM tenants WHERE cluster IS NOT NULL AND tenant_number IS NULL ORDER BY created_at, id"))]
        for offset, tenant_id in enumerate(ids, start=1):
            conn.execute(text("UPDATE tenants SET tenant_number = :n WHERE id = :i"),
                         {"n": start + offset, "i": tenant_id})
            numbered += 1
    return numbered
