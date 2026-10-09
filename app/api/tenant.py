import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
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
from app.services import helm_values, preflight, progress, provisioner, vault_service
from app.services.validation import ValidationError, validate_create_request

logger = logging.getLogger("tenant-operator.api.tenant")
router = APIRouter(prefix="/tenant", tags=["tenant"])
settings = get_settings()


@router.post(
    "",
    response_model=TenantCreateAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create (onboard) a tenant",
    description=(
        "Validates the request, allocates a sequential number, stores the tenant row as `PENDING` and "
        "returns **immediately** (202). Provisioning continues in the background: pick a spoke cluster, "
        "write Vault secrets, commit the per-tenant `values.yaml` to the GitOps repo, let Argo CD sync it, "
        "then seed the tenant database.\n\n"
        "Poll `GET /tenant/{id}` (or subscribe on Socket.IO) to follow progress.\n\n"
        "`password` is the tenant admin's initial password; it is used in memory only and never stored."
    ),
    responses={400: {"description": "Validation failed (bad name, duplicate active tenant, unknown service, ...)"}, **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}},
)
def create_tenant(
    req: TenantCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        validate_create_request(req, db)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Look for ANY trace of this tenant id (SQL/Mongo database, namespace on any
    # spoke, Argo CD application, ...) before creating anything -- not even the
    # tenant's row in our own database -- so the caller gets an immediate error
    # and nothing is left behind or touched on an existing tenant.
    existing, unverified = preflight.check_tenant_exists(req.tenantId, settings.environment)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"tenant id '{req.tenantId}' already exists: " + "; ".join(existing) + ". Nothing was created.",
        )
    if unverified:
        raise HTTPException(
            status_code=503,
            detail=f"could not verify that tenant id '{req.tenantId}' is unused, so nothing was created: "
                   + "; ".join(unverified),
        )

    # No sequence number is allocated any more: tenant_seq stays NULL, so the
    # tenant's slug (namespace, Vault path, git file, ...) is just its bare name.
    # Name collisions with older, sequence-suffixed tenants are rejected in
    # validate_create_request. See Tenant.slug.

    tenant = Tenant(
        tenant_name=req.tenantId,
        tenant_seq=None,
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


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get one tenant",
    description="Current record for a tenant, including `status`, `progress` (0-100, for a progress bar), `errorMessage` when FAILED, cluster and namespace.",
    responses={404: {"description": "Tenant not found"}, **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}},
)
def get_tenant(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant


@router.get(
    "/{tenant_id}/progress",
    summary="Step-by-step creation progress",
    description=(
        "The tenant's creation broken into ordered steps (validate, select cluster, check for existing "
        "resources, register, secrets, manifest, databases, deploy, eRep, ready), each with a state "
        "(`pending`, `running`, `done`, `warn`, `skipped`, `failed`), timings and the latest message, plus an "
        "overall `percent` and the raw `events` log. `hasDetail` is false for tenants created before step "
        "tracking existed -- use `GET /tenant/{id}` for those."
    ),
    responses={404: {"description": "Tenant not found"}, 401: {"description": "Missing/invalid token or API key"}},
)
def get_tenant_progress(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return progress.build_progress(db, tenant)


@router.get(
    "/{tenant_id}/vault",
    summary="Get a tenant's Vault secrets",
    description="Current Vault secrets for the tenant, one path per qraie-bridge service (platform defaults merged with tenant-specific generated credentials). **Returns sensitive values.**",
    responses={404: {"description": "Tenant not found"}, **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}},
)
def get_tenant_vault(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    """Current Vault secrets for this tenant -- one path per qraie-bridge
    chart service, platform defaults + tenant-specific (generated
    credential) fields merged together at onboarding time. See
    vault_service.py."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return vault_service.read_tenant_secrets(tenant.slug)


@router.get(
    "",
    response_model=list[TenantResponse],
    summary="List tenants",
    description="All tenants (including DELETED ones), newest first. Filter with `environment` and/or `status_filter`.",
    responses={401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}},
)
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


@router.put(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update a tenant",
    description="Change version, user count, database size or the enabled-service selection. Only allowed while the tenant is `RUNNING` or `FAILED`. Returns the current record immediately; the change is applied in the background (status goes to `UPDATING`).",
    responses={404: {"description": "Tenant not found"}, 409: {"description": "Tenant is not RUNNING/FAILED"}, **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}},
)
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


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete a tenant",
    description="Starts teardown in the background: removes the tenant's manifest from git, lets Argo CD prune it and deletes the namespace. Status goes to `DELETING`, then `DELETED` (the row is soft-deleted). **Destructive.**",
    responses={404: {"description": "Tenant not found"}, 409: {"description": "Deletion already in progress"}, **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}},
)
def delete_tenant(tenant_id: uuid.UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status == TenantStatus.DELETING:
        raise HTTPException(status_code=409, detail="deletion already in progress")

    background_tasks.add_task(provisioner.delete_tenant, tenant.id)
    return {"tenantId": tenant.id, "status": "DELETING"}
