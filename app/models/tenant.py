import dataclasses
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional


class TenantStatus(str, enum.Enum):
    PENDING = "PENDING"                # row created, not yet acted on
    VALIDATING = "VALIDATING"
    GIT_COMMITTED = "GIT_COMMITTED"     # values.yaml pushed to git
    SYNCING = "SYNCING"                 # Argo CD is applying it
    RUNNING = "RUNNING"                 # deployment healthy
    UPDATING = "UPDATING"
    DELETING = "DELETING"
    DELETED = "DELETED"
    FAILED = "FAILED"


# Rough progress heuristic for a loading bar -- not meant to be precise
# (SYNCING can take anywhere from seconds to the full provisioning timeout),
# just monotonically increasing so a frontend has something to show. Used by
# both the plain GET response (TenantResponse.progress) and SSE events
# (status_bus.build_event) so polling and streaming clients agree.
STATUS_PROGRESS: dict[TenantStatus, int] = {
    TenantStatus.PENDING: 0,
    TenantStatus.VALIDATING: 15,
    TenantStatus.GIT_COMMITTED: 40,
    TenantStatus.SYNCING: 70,
    TenantStatus.RUNNING: 100,
    TenantStatus.UPDATING: 50,
    TenantStatus.DELETING: 50,
    TenantStatus.DELETED: 100,
    TenantStatus.FAILED: 100,
}

# Closes the onboarding push subscription (status_bus.py) once the tenant
# reaches one of these.
TERMINAL_STATUSES = {TenantStatus.RUNNING, TenantStatus.FAILED, TenantStatus.DELETED}


@dataclasses.dataclass
class Tenant:
    """
    One document in the `tenants` collection of tenant-operator's own
    dedicated MongoDB instance (app/database.py) -- this operator's
    bookkeeping/state store, NOT the separate shared MongoDB instance used
    for tenant application data + the env-config mirror (mongo_service.py,
    doc/MONGODB_SETUP.md). Two unrelated MongoDB deployments, easy to
    conflate by name alone.

    Plain dataclass rather than an ORM model -- Mongo has no schema to
    declare against. `_id` in the stored document is this dataclass's `id`
    (str(uuid.uuid4()) on the wire, a real uuid.UUID on this object) so
    TenantResponse (schemas.py, from_attributes=True) keeps working
    unchanged: it only needs plain attribute access, not any particular ORM.
    """
    # NOT unique in Mongo on purpose: a DELETED tenant's name must be
    # reusable (soft-delete, not hard-delete -- see deleted_at). Uniqueness
    # among *active* tenants is enforced in validation.py instead. Single-
    # replica assumption already documented elsewhere in this codebase
    # (README) makes the race window between that check and the insert
    # acceptable for now.
    tenant_name: str
    environment: str

    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    # Short sequential number this operator allocates at creation time (see
    # database.next_tenant_seq -- an atomic Mongo counter, replacing the
    # SQL-era `SELECT MAX(tenant_seq)+1` race) -- NOT the caller-supplied
    # `tenantId` request field (that's actually a human-chosen name, stored
    # in tenant_name above; the wire field is named `tenantId` for
    # historical reasons -- see schemas.TenantCreateRequest). Exists so
    # `slug` below stays unique and sortable even across thousands of
    # tenants with similar names ("acme" vs "acme-2024" don't collide;
    # "acme-corp-42" tells an operator at a glance this was the 42nd tenant
    # ever created).
    tenant_seq: Optional[int] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    domain: Optional[str] = None
    tier: str = "standard"

    # Which chart this tenant is deployed with. Always "qraie-bridge" (the
    # ~30-service prototype-bridge conversion) for every tenant created from
    # here on -- there used to be a second value, "workplace" (a toy
    # single-image demo/POC chart, poc/chart-workplace), selectable via the
    # request's `appType` field, but that chart was never used for a real
    # tenant and has been retired along with the code that branched on this
    # field. Kept as a real field (rather than dropped) so any pre-existing
    # document and API consumers reading `TenantResponse.appType` don't
    # break -- see helm_values.render_qraie_bridge_values_yaml().
    app_type: str = "qraie-bridge"
    # Names from helm_values.QRAIE_BRIDGE_SERVICE_NAMES this tenant does NOT
    # get. Set from the request's `services: {name: bool}` field (any name
    # whose value is false), see api/tenant.py's create_tenant. Rendered
    # into the per-tenant values.yaml as the chart's `disabledServices:`
    # list, NOT merged back into `services:` -- Helm replaces an entire list
    # wholesale on override, so a per-tenant override can't toggle one entry
    # inside the chart's base 32-entry `services:` array without repeating
    # the whole thing; a separate plain list merges cleanly instead.
    disabled_services: Optional[list[str]] = None
    # Vestigial: originally the retired "workplace" chart's image.repository/
    # image.tag (see app_type above). Not read anywhere in the remaining
    # qraie-bridge path -- every service's image tag comes from that chart's
    # own defaults instead (see its README "Image versioning" section).
    # Kept as fields (rather than dropped) and populated from
    # settings.default_application/default_version at creation time; no
    # longer settable per-request.
    application: str = ""
    version: str = ""
    users: int = 0
    database_size: str = "10Gi"

    cluster: Optional[str] = None
    namespace: Optional[str] = None

    # Tenant CR created on the hub cluster before GitOps takes over.
    hub_cr_name: Optional[str] = None
    hub_cr_uid: Optional[str] = None

    status: TenantStatus = TenantStatus.PENDING
    error_message: Optional[str] = None

    git_commit: Optional[str] = None
    helm_chart_version: Optional[str] = None

    created_by: Optional[str] = None
    created_at: datetime = dataclasses.field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = dataclasses.field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = None

    @property
    def slug(self) -> str:
        """The one identifier used everywhere this tenant needs naming
        outside its own database document: Kubernetes namespace, Vault
        secret path, git filename / Argo CD Application name, Tenant CR
        name, meta-builder Job name. Suffixes the human-chosen tenant_name
        with the operator-allocated sequential number so it stays unique
        even though tenant_name has no uniqueness constraint -- "acme-
        corp-42" keeps the name fully readable up front, with the number
        only there to break a collision.

        Deliberately NOT used for anything customer-facing (tenant.domain,
        the rendered chart's public URL, the seeded database's display
        name) -- those stay on bare tenant_name so a customer never sees
        an internal sequence number in their own URL.

        Truncates tenant_name to fit Kubernetes' 63-char namespace limit
        once the "tenant-" prefix (see provisioner.py) and this sequence
        suffix are both accounted for, rather than risk generating a
        namespace name Kubernetes silently rejects.

        Falls back to bare tenant_name if tenant_seq was never allocated
        (pre-migration documents) so nothing crashes on old data.
        """
        if self.tenant_seq is None:
            return self.tenant_name
        suffix = f"-{self.tenant_seq}"
        max_name_len = 63 - len("tenant-") - len(suffix)
        name = self.tenant_name[:max_name_len].rstrip("-")
        return f"{name}{suffix}"

    def to_doc(self) -> dict:
        """Serialize to the dict stored in Mongo -- `_id` is this tenant's
        `id` as a string, `status` its plain enum value."""
        doc = dataclasses.asdict(self)
        doc["_id"] = str(doc.pop("id"))
        doc["status"] = self.status.value
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "Tenant":
        """Hydrate from a raw Mongo document (as returned by find/find_one)."""
        fields = dict(doc)
        fields["id"] = uuid.UUID(fields.pop("_id"))
        fields["status"] = TenantStatus(fields["status"])
        fields.pop("_class", None)  # tolerate a stray discriminator field if ever added
        return cls(**fields)

    def __repr__(self) -> str:
        return f"<Tenant {self.tenant_name} status={self.status}>"
