"""
tenant-operator's own bookkeeping store -- a dedicated MongoDB instance
(DATABASE_MONGO_URI/DATABASE_MONGO_DB_NAME) holding this operator's `tenants`
and `counters` collections. Deliberately a SEPARATE deployment from the
shared MongoDB instance used for tenant application data + the env-config
mirror (mongo_service.py, settings.mongo_env_config_uri, doc/
MONGODB_SETUP.md) -- that one holds per-tenant application data on the
spoke; this one is the operator's own control-plane state on the hub, and
the operator needs to function (list/track tenants) even if the spoke or
ClusterMesh to it is unreachable.

pymongo's MongoClient is thread-safe and pools connections internally, so
unlike the old SQLAlchemy SessionLocal() there's no per-request session
object to create/close -- `db` below is a single shared handle for the
whole process lifetime.
"""
from pymongo import MongoClient, ReturnDocument
from pymongo.database import Database

from app.config import get_settings

settings = get_settings()

client = MongoClient(settings.database_mongo_uri)
db: Database = client[settings.database_mongo_db_name]

tenants = db["tenants"]
counters = db["counters"]


def get_db():
    """FastAPI dependency: yields the shared Database handle."""
    yield db


def ensure_indexes() -> None:
    """Called once at startup (app/main.py) -- Mongo creates collections
    implicitly on first write, so this only needs to declare indexes.
    Equivalent of the old Base.metadata.create_all() for schema-on-write
    Mongo: nothing to migrate, just indexes to keep queries backed by them."""
    tenants.create_index("tenant_name")
    tenants.create_index("tenant_seq")
    tenants.create_index("cluster")
    tenants.create_index("status")
    tenants.create_index("environment")


def next_tenant_seq() -> int:
    """Atomically allocates the next sequential tenant number via a single
    findAndModify on a dedicated counters document -- replaces the old
    `SELECT MAX(tenant_seq)+1` (documented elsewhere as an accepted
    single-replica race). This is a genuine improvement, not just a
    like-for-like port: Mongo's findOneAndUpdate with $inc is atomic even
    under concurrent callers, so the race window closes entirely, not just
    "acceptable under a single-replica assumption"."""
    doc = counters.find_one_and_update(
        {"_id": "tenant_seq"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["value"]
