"""Request-level validation, independent of cluster placement."""
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.tenant import Tenant, TenantStatus
from app.models.schemas import TenantCreateRequest

settings = get_settings()


class ValidationError(Exception):
    """Raised for any 400-worthy problem with a tenant request."""


def validate_create_request(req: TenantCreateRequest, db: Session) -> None:
    # Duplicate tenant id (globally unique across all clusters/environments
    # among tenants that still exist -- a DELETED tenant's name is free to
    # reuse, since it maps 1:1 to a namespace name and an Argo CD
    # Application name that no longer exist either).
    existing = (
        db.query(Tenant)
        .filter(Tenant.tenant_name == req.tenantId, Tenant.status != TenantStatus.DELETED)
        .first()
    )
    if existing is not None:
        raise ValidationError(f"tenant '{req.tenantId}' already exists (status={existing.status})")

    # New tenants have no sequence suffix, so their slug IS their name. It must
    # not equal the slug of an older tenant that still exists -- e.g. a new
    # tenant "acme-2" would collide with the existing "acme" (seq 2), whose slug
    # is "acme-2" (same namespace, Vault path, git file and Argo CD Application).
    for other in db.query(Tenant).filter(Tenant.status != TenantStatus.DELETED).all():
        if other.slug == req.tenantId:
            raise ValidationError(
                f"tenant '{req.tenantId}' would collide with the existing tenant '{other.tenant_name}' "
                f"(slug '{other.slug}', same namespace/Vault path) -- choose a different tenantId"
            )

    if settings.environment not in settings.allowed_environments:
        raise ValidationError(
            f"this operator's environment '{settings.environment}' is not in allowed list "
            f"{settings.allowed_environments}"
        )
