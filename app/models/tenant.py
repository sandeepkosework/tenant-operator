import enum
import uuid

from sqlalchemy import Column, String, Integer, DateTime, Enum, Text, JSON, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


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


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NOT DB-unique on purpose: a DELETED tenant's name must be reusable
    # (soft-delete, not hard-delete -- see deleted_at), and a partial unique
    # index ("unique among non-DELETED rows") isn't portably expressible
    # across both SQLite (local/POC) and Postgres (real deployments) via
    # plain SQLAlchemy. Uniqueness among *active* tenants is enforced in
    # validation.py instead. Single-replica assumption already documented
    # elsewhere in this codebase (see README) makes the race window between
    # that check and the insert acceptable for now.
    tenant_name = Column(String(63), nullable=False, index=True)
    # Short sequential number this operator allocates at creation time (see
    # api/tenant.py's create_tenant) -- NOT the caller-supplied `tenantId`
    # request field (that's actually a human-chosen name, stored in
    # tenant_name above; the wire field is named `tenantId` for historical
    # reasons -- see schemas.TenantCreateRequest). Exists so `slug` below
    # stays unique and sortable even across thousands of tenants with
    # similar names ("acme" vs "acme-2024" don't collide; "00042-acme"
    # tells an operator at a glance this was the 42nd tenant ever created).
    # Same no-DB-unique-constraint reasoning as tenant_name applies here --
    # allocated via SELECT MAX()+1 at create time, acceptable under the
    # single-replica assumption already documented elsewhere in this
    # codebase. Nullable so old rows created before this column existed
    # don't break; every tenant created going forward always gets one.
    tenant_seq = Column(Integer, nullable=True, index=True)
    display_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    domain = Column(String(255), nullable=True)
    tier = Column(String(32), default="standard")

    environment = Column(String(32), nullable=False)
    # Which chart this tenant is deployed with. Always "qraie-bridge" (the
    # ~30-service prototype-bridge conversion) for every tenant created from
    # here on -- there used to be a second value, "workplace" (a toy
    # single-image demo/POC chart, poc/chart-workplace), selectable via the
    # request's `appType` field, but that chart was never used for a real
    # tenant and has been retired along with the code that branched on this
    # column. Kept as a real column (rather than dropped) so any pre-existing
    # row and API consumers reading `TenantResponse.appType` don't break --
    # see helm_values.render_qraie_bridge_values_yaml().
    app_type = Column(String(32), nullable=False, default="qraie-bridge", server_default="qraie-bridge")
    # Names from helm_values.QRAIE_BRIDGE_SERVICE_NAMES this tenant does NOT
    # get. Set from the request's `services: {name: bool}` field (any name
    # whose value is false), see api/tenant.py's create_tenant. Rendered
    # into the per-tenant values.yaml as the chart's `disabledServices:`
    # list, NOT merged back into `services:` -- Helm replaces an entire list
    # wholesale on override, so a per-tenant override can't toggle one entry
    # inside the chart's base 32-entry `services:` array without repeating
    # the whole thing; a separate plain list merges cleanly instead.
    disabled_services = Column(JSON, nullable=True)
    # Vestigial: originally the retired "workplace" chart's image.repository/
    # image.tag (see app_type above). Not read anywhere in the remaining
    # qraie-bridge path -- every service's image tag comes from that chart's
    # own defaults instead (see its README "Image versioning" section).
    # Kept as NOT NULL DB columns (rather than dropped, which would need a
    # migration) and populated from settings.default_application/
    # default_version at creation time; no longer settable per-request.
    application = Column(String(64), nullable=False)
    version = Column(String(32), nullable=False)
    users = Column(Integer, default=0)
    database_size = Column(String(16), default="10Gi")

    cluster = Column(String(64), nullable=True)
    namespace = Column(String(64), nullable=True)

    # Tenant CR created on the hub cluster before GitOps takes over.
    hub_cr_name = Column(String(64), nullable=True)
    hub_cr_uid = Column(String(64), nullable=True)

    status = Column(Enum(TenantStatus), default=TenantStatus.PENDING, nullable=False)
    error_message = Column(Text, nullable=True)

    git_commit = Column(String(64), nullable=True)
    helm_chart_version = Column(String(32), nullable=True)

    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    @property
    def slug(self) -> str:
        """The one identifier used everywhere this tenant needs naming
        outside its own database row: Kubernetes namespace, Vault secret
        path, git filename / Argo CD Application name, Tenant CR name,
        meta-builder Job name. Suffixes the human-chosen tenant_name with
        the operator-allocated sequential number so it stays unique even
        though tenant_name has no DB constraint -- "acme-corp-42" keeps the
        name fully readable up front, with the number only there to break
        a collision.

        Deliberately NOT used for anything customer-facing (tenant.domain,
        the rendered chart's public URL, the seeded database's display
        name) -- those stay on bare tenant_name so a customer never sees
        an internal sequence number in their own URL.

        Truncates tenant_name to fit Kubernetes' 63-char namespace limit
        once the "tenant-" prefix (see provisioner.py) and this sequence
        suffix are both accounted for, rather than risk generating a
        namespace name Kubernetes silently rejects.

        Falls back to bare tenant_name if tenant_seq was never allocated
        (pre-migration rows) so nothing crashes on old data.
        """
        if self.tenant_seq is None:
            return self.tenant_name
        suffix = f"-{self.tenant_seq}"
        max_name_len = 63 - len("tenant-") - len(suffix)
        name = self.tenant_name[:max_name_len].rstrip("-")
        return f"{name}{suffix}"

    def __repr__(self) -> str:
        return f"<Tenant {self.tenant_name} status={self.status}>"
