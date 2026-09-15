"""
Repository layer for the `tenants` collection -- replaces the SQLAlchemy
`db.query(Tenant)...` calls that used to be scattered across api/tenant.py,
provisioner.py, socketio_app.py, cluster_selector.py, spoke_cr.py and
validation.py. Every function takes the pymongo Database handle from
app.database.get_db() (or app.database.db directly, for non-request
contexts like the background provisioning workflow) and returns/accepts
plain app.models.tenant.Tenant objects -- callers never see raw Mongo
documents.
"""
import uuid
from datetime import datetime, timezone

from pymongo.database import Database

from app.models.tenant import Tenant, TenantStatus

# Statuses that count as "this tenant is currently occupying a slot" --
# duplicated from cluster_selector.ACTIVE_STATUSES here would create an
# import cycle (cluster_selector already imports this module), so it's
# defined once in cluster_selector and passed in by callers that need it.


def get_by_id(db: Database, tenant_id: uuid.UUID) -> Tenant | None:
    doc = db["tenants"].find_one({"_id": str(tenant_id)})
    return Tenant.from_doc(doc) if doc else None


def find_active_by_name(db: Database, tenant_name: str) -> Tenant | None:
    """Most recent non-DELETED tenant with this name, if any -- used to
    reject duplicate onboarding requests. See Tenant.tenant_name's
    docstring for why this isn't a hard uniqueness constraint."""
    doc = db["tenants"].find_one({
        "tenant_name": tenant_name,
        "status": {"$ne": TenantStatus.DELETED.value},
    })
    return Tenant.from_doc(doc) if doc else None


def list_tenants(
    db: Database,
    environment: str | None = None,
    status: TenantStatus | None = None,
) -> list[Tenant]:
    query: dict = {}
    if environment:
        query["environment"] = environment
    if status:
        query["status"] = status.value
    cursor = db["tenants"].find(query).sort("created_at", -1)
    return [Tenant.from_doc(doc) for doc in cursor]


def list_active_by_cluster(db: Database, cluster_name: str, active_statuses: tuple[TenantStatus, ...]) -> list[Tenant]:
    cursor = db["tenants"].find({
        "cluster": cluster_name,
        "status": {"$in": [s.value for s in active_statuses]},
    }).sort("tenant_name", 1)
    return [Tenant.from_doc(doc) for doc in cursor]


def count_active_by_cluster(db: Database, active_statuses: tuple[TenantStatus, ...]) -> dict[str, int]:
    """{cluster_name: count} across every tenant currently occupying a slot
    -- replaces the old `db.query(Tenant.cluster, func.count(...)).group_by(...)`."""
    pipeline = [
        {"$match": {"status": {"$in": [s.value for s in active_statuses]}}},
        {"$group": {"_id": "$cluster", "count": {"$sum": 1}}},
    ]
    return {doc["_id"]: doc["count"] for doc in db["tenants"].aggregate(pipeline) if doc["_id"] is not None}


def insert(db: Database, tenant: Tenant) -> None:
    db["tenants"].insert_one(tenant.to_doc())


def save(db: Database, tenant: Tenant) -> None:
    """Full-document replace, keyed by _id -- the Mongo equivalent of the
    old `db.add(tenant); db.commit(); db.refresh(tenant)` pattern. Bumps
    updated_at the same way the old server_default=func.now(), onupdate=
    func.now() column did."""
    tenant.updated_at = datetime.now(timezone.utc)
    db["tenants"].replace_one({"_id": str(tenant.id)}, tenant.to_doc(), upsert=True)
