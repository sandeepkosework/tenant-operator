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
    timestamp = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
    for column, column_type, why in (
        ("archived_at", timestamp, "archive/restore will not work"),
        ("cluster_seq", "INTEGER", "tenants will have no number on their cluster"),
    ):
        try:
            if column not in {c["name"] for c in inspect(engine).get_columns(table)}:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
                log.info("added column %s.%s", table, column)
        except Exception:  # noqa: BLE001
            log.exception("could not add %s.%s -- %s until it exists", table, column, why)

    try:
        backfill_cluster_seq()
    except Exception:  # noqa: BLE001
        log.exception("could not number the existing tenants on their clusters")

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


def backfill_cluster_seq() -> int:
    """Give every tenant that has a cluster but no cluster_seq its number: in creation order, after the
    highest number already used on that cluster. Returns how many were numbered; 0 when there is nothing
    to do, so it is safe to run at every start."""
    from sqlalchemy import text

    numbered = 0
    with engine.begin() as conn:
        clusters = [row[0] for row in conn.execute(
            text("SELECT DISTINCT cluster FROM tenants WHERE cluster IS NOT NULL AND cluster_seq IS NULL"))]
        for cluster in clusters:
            highest = conn.execute(text("SELECT COALESCE(MAX(cluster_seq), 0) FROM tenants WHERE cluster = :c"),
                                   {"c": cluster}).scalar()
            ids = [row[0] for row in conn.execute(
                text("SELECT id FROM tenants WHERE cluster = :c AND cluster_seq IS NULL ORDER BY created_at, id"),
                {"c": cluster})]
            for offset, tenant_id in enumerate(ids, start=1):
                conn.execute(text("UPDATE tenants SET cluster_seq = :n WHERE id = :i"),
                             {"n": highest + offset, "i": tenant_id})
                numbered += 1
    return numbered
