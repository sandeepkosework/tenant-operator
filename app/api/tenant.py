import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pymongo.database import Database

from app.config import get_settings
from app.database import get_db, next_tenant_seq
from app.models.tenant import Tenant, TenantStatus
from app.models.schemas import (
    TenantCreateAccepted,
    TenantCreateRequest,
    TenantResponse,
    TenantUpdateRequest,
)
from app.services import helm_values, provisioner, tenant_repo, vault_service
from app.services.validation import ValidationError, validate_create_request

logger = logging.getLogger("tenant-operator.api.tenant")
router = APIRouter(prefix="/tenant", tags=["tenant"])
settings = get_settings()


@router.post("", response_model=TenantCreateAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_tenant(
    req: TenantCreateRequest,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
):
    try:
        validate_create_request(req, db)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Allocated via an atomic Mongo counter (app.database.next_tenant_seq) --
    # see Tenant.tenant_seq's comment for why this needs to stay unique and
    # sortable even across thousands of tenants with similar names.
    next_seq = next_tenant_seq()

    tenant = Tenant(
        tenant_name=req.tenantId,
        tenant_seq=next_seq,
        display_name=req.displayName,
        email=req.email,
        domain=req.domain,
        tier=req.tier or settings.default_tier,
        environment=settings.environment,  # which environment this deployment serves, not from the payload
        app_type="qraie-bridge",  # the only chart tenants are provisioned onto -- see Tenant.app_type
        disabled_services=helm_values.compute_disabled_services(req.services, req.onlyListedServices),
        application=settings.default_application,
        version=settings.default_version,
        users=req.users,
        database_size=req.database.size,
        status=TenantStatus.PENDING,
        created_by=req.createdBy,
    )
    tenant_repo.insert(db, tenant)
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
def get_tenant(tenant_id: uuid.UUID, db: Database = Depends(get_db)):
    tenant = tenant_repo.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant


@router.get("/{tenant_id}/vault")
def get_tenant_vault(tenant_id: uuid.UUID, db: Database = Depends(get_db)):
    """Current Vault secrets for this tenant -- one path per qraie-bridge
    chart service, platform defaults + tenant-specific (generated
    credential) fields merged together at onboarding time. See
    vault_service.py."""
    tenant = tenant_repo.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return vault_service.read_tenant_secrets(tenant.slug)


@router.get("", response_model=list[TenantResponse])
def list_tenants(
    environment: str | None = None,
    status_filter: TenantStatus | None = None,
    db: Database = Depends(get_db),
):
    return tenant_repo.list_tenants(db, environment=environment, status=status_filter)


@router.put("/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: uuid.UUID,
    req: TenantUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
):
    tenant = tenant_repo.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status not in (TenantStatus.RUNNING, TenantStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"tenant is currently {tenant.status}; updates are only allowed once RUNNING or FAILED",
        )
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
def delete_tenant(tenant_id: uuid.UUID, background_tasks: BackgroundTasks, db: Database = Depends(get_db)):
    tenant = tenant_repo.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status == TenantStatus.DELETING:
        raise HTTPException(status_code=409, detail="deletion already in progress")

    background_tasks.add_task(provisioner.delete_tenant, tenant.id)
    return {"tenantId": tenant.id, "status": "DELETING"}
