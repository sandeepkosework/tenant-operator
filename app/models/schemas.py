import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.models.tenant import STATUS_PROGRESS, TenantStatus
from app.services.helm_values import QRAIE_BRIDGE_SERVICE_NAMES


class DatabaseSpec(BaseModel):
    size: str = Field(default="10Gi", pattern=r"^\d+[GMT]i$")


class TenantCreateRequest(BaseModel):
    """
    Onboarding payload. `environment` is intentionally NOT a field here --
    each tenant-operator deployment serves exactly one environment (prod or
    stage), set via the `environment` env var (see app/config.py), so which
    environment a tenant lands in is a deployment-time fact, not per-request.
    """
    # max_length is NOT 63 despite tenant_name's column width -- the
    # namespace this becomes part of is "tenant-<this>-<42>", and
    # Kubernetes namespaces cap at 63 chars total. 63 - len("tenant-") -
    # len("-42") <= 50 for any realistic sequence number; see Tenant.slug
    # for where that budget is spent (and truncated further if needed).
    tenantId: str = Field(..., min_length=3, max_length=50)
    displayName: str = Field(..., min_length=1, max_length=255)
    email: str
    password: str = Field(..., min_length=8)
    domain: str

    # Every tenant is provisioned onto the ~30-service chart converted from
    # the prototype-bridge docker-compose stack (helm-chart-bridge). There
    # used to be a second, generic single-image "workplace" chart selectable
    # via an `appType` request field (with `application`/`version` fields
    # mapping to its image.repository/image.tag) -- that chart was only ever
    # a demo/POC (poc/chart-workplace) and has been retired, along with the
    # request fields that only made sense for it. `Tenant.app_type` still
    # exists as a DB column (always "qraie-bridge" for every tenant created
    # from here on) -- see that column's docstring.
    tier: Optional[str] = None
    users: int = Field(default=0, ge=0)
    database: DatabaseSpec = DatabaseSpec()
    createdBy: Optional[str] = None

    # Which of the chart's ~30 services this tenant actually gets. Sparse
    # and opt-out by default (onlyListedServices=false) -- any service not
    # mentioned here still gets created (today's default behavior is
    # unchanged for every existing caller that doesn't pass this field at
    # all). e.g. {"voxflow": false, "mcp-server": false} skips just those two.
    # Set onlyListedServices=true to flip to opt-IN / allowlist instead --
    # every service NOT set to `true` here is disabled, so
    # {"services": {"bridge": true}, "onlyListedServices": true} runs only
    # `bridge`, without spelling out the other ~29 as `false`.
    # See helm_values.compute_disabled_services() and
    # charts/qraie-bridge/values.yaml's `disabledServices`.
    services: dict[str, bool] = Field(default_factory=dict)
    onlyListedServices: bool = False

    @field_validator("tenantId")
    @classmethod
    def validate_tenant_id(cls, v: str) -> str:
        import re
        v = v.lower()
        if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", v):
            raise ValueError(
                "tenantId must be lowercase alphanumeric with optional hyphens "
                "(RFC-1123 label, since it becomes a namespace suffix)"
            )
        return v

    @model_validator(mode="after")
    def validate_services(self) -> "TenantCreateRequest":
        if not self.services and not self.onlyListedServices:
            return self
        unknown = set(self.services) - QRAIE_BRIDGE_SERVICE_NAMES
        if unknown:
            raise ValueError(f"unknown qraie-bridge service name(s): {sorted(unknown)}")
        if self.onlyListedServices and not any(self.services.values()):
            raise ValueError(
                "onlyListedServices=true with no service set to true would disable every service -- "
                "set at least one service to true, e.g. {\"bridge\": true}"
            )
        return self

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        import re
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("email must be a valid email address")
        return v


class TenantUpdateRequest(BaseModel):
    version: Optional[str] = None
    users: Optional[int] = Field(default=None, ge=0)
    database: Optional[DatabaseSpec] = None
    # None = leave this tenant's service selection untouched; {} = clear
    # back to every service enabled; see TenantCreateRequest.services --
    # same semantics (including onlyListedServices below), just optional
    # here since most updates don't touch it.
    services: Optional[dict[str, bool]] = None
    onlyListedServices: bool = False

    @model_validator(mode="after")
    def validate_services(self) -> "TenantUpdateRequest":
        if self.services is None:
            return self
        unknown = set(self.services) - QRAIE_BRIDGE_SERVICE_NAMES
        if unknown:
            raise ValueError(f"unknown qraie-bridge service name(s): {sorted(unknown)}")
        if self.onlyListedServices and not any(self.services.values()):
            raise ValueError(
                "onlyListedServices=true with no service set to true would disable every service -- "
                "set at least one service to true, e.g. {\"bridge\": true}"
            )
        return self


class TenantResponse(BaseModel):
    id: uuid.UUID
    tenantName: str = Field(validation_alias="tenant_name")
    tenantSeq: Optional[int] = Field(default=None, validation_alias="tenant_seq")
    # Read via getattr(orm_obj, "slug") -- Tenant.slug is a plain @property,
    # not a column, but from_attributes=True below still picks it up.
    slug: str
    displayName: Optional[str] = Field(default=None, validation_alias="display_name")
    domain: Optional[str] = None
    tier: Optional[str] = None
    environment: str
    appType: str = Field(validation_alias="app_type")
    disabledServices: list[str] = Field(default_factory=list, validation_alias="disabled_services")

    @field_validator("disabledServices", mode="before")
    @classmethod
    def _coerce_disabled_services(cls, v):
        # DB column is nullable (NULL for workplace tenants, or any
        # qraie-bridge tenant created before this field existed) --
        # normalize to [] rather than let a NULL fail list[str] validation.
        return v or []

    application: str
    version: str
    users: int
    cluster: Optional[str] = None
    namespace: Optional[str] = None
    status: TenantStatus
    errorMessage: Optional[str] = Field(default=None, validation_alias="error_message")
    gitCommit: Optional[str] = Field(default=None, validation_alias="git_commit")
    hubCrName: Optional[str] = Field(default=None, validation_alias="hub_cr_name")
    hubCrUid: Optional[str] = Field(default=None, validation_alias="hub_cr_uid")
    createdAt: datetime = Field(validation_alias="created_at")
    updatedAt: datetime = Field(validation_alias="updated_at")

    model_config = {"from_attributes": True, "populate_by_name": True}

    @computed_field
    @property
    def progress(self) -> int:
        """Rough 0-100 heuristic for a loading bar -- see STATUS_PROGRESS."""
        return STATUS_PROGRESS.get(self.status, 0)


class TenantCreateAccepted(BaseModel):
    tenantId: uuid.UUID
    status: TenantStatus
