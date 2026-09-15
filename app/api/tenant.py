import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.tenant import Tenant, TenantStatus
from app.models.schemas import (
    TenantCreateAccepted,
    TenantCreateRequest,
    TenantResponse,
    TenantUpdateRequest,
)
from app.services import helm_values, provisioner, vault_service
from app.services.validation import ValidationError, validate_create_request

logger = logging.getLogger("tenant-operator.api.tenant")
router = APIRouter(prefix="/tenant", tags=["tenant"])
settings = get_settings()


@router.post("", response_model=TenantCreateAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_tenant(
    req: TenantCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        validate_create_request(req, db)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Allocated here, not left to a DB default, since it must be a plain
    # sequential int portable across SQLite (local/POC) and Postgres (real
    # deployments) -- see Tenant.tenant_seq's comment for the accepted
    # single-replica race-window tradeoff, same one tenant_name uniqueness
    # already lives with elsewhere in this codebase.
    next_seq = (db.query(func.max(Tenant.tenant_seq)).scalar() or 0) + 1

    tenant = Tenant(
        tenant_name=req.tenantId,
        tenant_seq=next_seq,
        display_name=req.displayName,
        email=req.email,
        domain=req.domain,
        tier=req.tier or settings.default_tier,
        environment=settings.environment,  # which environment this deployment serves, not from the payload
        app_type=req.appType,
        disabled_services=helm_values.compute_disabled_services(req.services, req.onlyListedServices),
        application=req.application or settings.default_application,
        version=req.version or settings.default_version,
        users=req.users,
        database_size=req.database.size,
        status=TenantStatus.PENDING,
        created_by=req.createdBy,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    logger.info(
        "[step] tenant=%s (slug=%s) row created in tenant-operator DB (id=%s, environment=%s)",
        tenant.tenant_name, tenant.slug, tenant.id, tenant.environment,
    )

    # Hand off to the async provisioning workflow; the request returns immediately.
    # req.password is passed through only in-memory for the meta-builder Job
    # trigger later in the flow -- it is never persisted to the tenant row.
    background_tasks.add_task(provisioner.provision_tenant, tenant.id, req.password)

    return TenantCreateAccepted(tenantId=tenant.id, status=tenant.status)


@router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant


@router.get("/{tenant_id}/vault")
def get_tenant_vault(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    """Current Vault secrets for this tenant (redis/database/mongo/jwt) --
    the common (host/port) + tenant-specific (generated credential) fields
    merged together at onboarding time. See vault_service.py."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return vault_service.read_tenant_secrets(tenant.slug, app_type=tenant.app_type)


@router.get("", response_model=list[TenantResponse])
def list_tenants(
    environment: str | None = None,
    status_filter: TenantStatus | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Tenant)
    if environment:
        query = query.filter(Tenant.environment == environment)
    if status_filter:
        query = query.filter(Tenant.status == status_filter)
    return query.order_by(Tenant.created_at.desc()).all()


@router.put("/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: uuid.UUID,
    req: TenantUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status not in (TenantStatus.RUNNING, TenantStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"tenant is currently {tenant.status}; updates are only allowed once RUNNING or FAILED",
        )
    if req.services is not None and tenant.app_type != "qraie-bridge":
        raise HTTPException(status_code=400, detail="services is only meaningful for appType=qraie-bridge")

    new_disabled_services = None
    if req.services is not None:
        new_disabled_services = helm_values.compute_disabled_services(req.services, req.onlyListedServices)

    background_tasks.add_task(
        provisioner.update_tenant,
        tenant.id,
        req.version,
        req.users,
        req.database.size if req.database else None,
        new_disabled_services,
    )
    return tenant


@router.delete("/{tenant_id}", status_code=status.HTTP_202_ACCEPTED)
def delete_tenant(tenant_id: uuid.UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status == TenantStatus.DELETING:
        raise HTTPException(status_code=409, detail="deletion already in progress")

    background_tasks.add_task(provisioner.delete_tenant, tenant.id)
    return {"tenantId": tenant.id, "status": "DELETING"}
