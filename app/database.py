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

    try:
        if "archived_at" not in {c["name"] for c in inspect(engine).get_columns(Tenant.__tablename__)}:
            column_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {Tenant.__tablename__} ADD COLUMN archived_at {column_type}"))
            log.info("added column %s.archived_at", Tenant.__tablename__)
    except Exception:  # noqa: BLE001
        log.exception("could not add tenants.archived_at -- archive/restore will not work until it exists")

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
