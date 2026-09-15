"""Request-level validation, independent of cluster placement."""
from pymongo.database import Database

from app.config import get_settings
from app.models.schemas import TenantCreateRequest
from app.services import tenant_repo

settings = get_settings()


class ValidationError(Exception):
    """Raised for any 400-worthy problem with a tenant request."""


def validate_create_request(req: TenantCreateRequest, db: Database) -> None:
    # Duplicate tenant id (globally unique across all clusters/environments
    # among tenants that still exist -- a DELETED tenant's name is free to
    # reuse, since it maps 1:1 to a namespace name and an Argo CD
    # Application name that no longer exist either).
    existing = tenant_repo.find_active_by_name(db, req.tenantId)
    if existing is not None:
        raise ValidationError(f"tenant '{req.tenantId}' already exists (status={existing.status})")

    if settings.environment not in settings.allowed_environments:
        raise ValidationError(
            f"this operator's environment '{settings.environment}' is not in allowed list "
            f"{settings.allowed_environments}"
        )
