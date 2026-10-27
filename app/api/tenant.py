import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
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
from app.services import chart_versions, helm_values, preflight, progress, provisioner, purge_service, vault_service
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
        "`chartVersion` is optional: omit it for the newest chart release, or name an older tag (like `v0.1.0`) to stay on it.\n\n"
        "`password` is the tenant admin's initial password; it is used in memory only and never stored."
    ),
    responses={400: {"description": "Validation failed (bad name, duplicate active tenant, unknown service, unknown chartVersion, ...)"}, **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}},
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

    try:
        chart_version = chart_versions.resolve_for_create(req.chartVersion)
    except chart_versions.ChartVersionError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

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
        # Pinned for the tenant's whole life: this is the chart release it is created on, and
        # it only changes through an update that passes a new chartVersion.
        helm_chart_version=chart_version,
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


@router.post(
    "/{tenant_id}/vault/prune-empty",
    summary="Remove empty keys from a tenant's Vault secrets",
    description=(
        "Removes keys whose value is an empty string from each of the tenant's Vault paths, so a value set once in "
        "`tenant-common` or `service-common` is no longer overridden by an empty key in a service's own secret. "
        "**Dry run by default** -- it only reports what it would remove; pass `apply=true` to rewrite the secrets "
        "(each affected path gets a new KV version with only its non-empty keys). A path where every key is empty "
        "is left alone. Afterwards the chart re-syncs within the hour (or force it) and the tenant's pods must be "
        "restarted to pick the change up."
    ),
    responses={404: {"description": "Tenant not found"}, 503: {"description": "Vault disabled or unreachable"},
               401: {"description": "Missing/invalid token or API key"}},
)
def prune_empty_vault_keys(tenant_id: uuid.UUID, apply: bool = False, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        return vault_service.prune_empty_tenant_secrets(tenant.slug, apply=apply)
    except vault_service.VaultServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post(
    "/{tenant_id}/vault/sync-shared-keys",
    summary="Move FABREQ_COMMAND_ENDPOINT_MAP and MCP_SERVER_URL into service-common",
    description=(
        "For a tenant created before these two keys moved to `service-common`: sets them there to the tenant's "
        "in-cluster addresses (`http://<tenant>-wfm-microservice:9001`, `http://<tenant>-mcp-server:10011/mcp`) and "
        "**removes them from every other Vault path of the tenant, whatever their value** -- including any "
        "service's own value (the report shows what was removed). **Dry run by default**; pass `apply=true` to write. "
        "Afterwards the chart re-syncs and the tenant's pods must be restarted."
    ),
    responses={404: {"description": "Tenant not found"}, 503: {"description": "Vault disabled or unreachable"},
               401: {"description": "Missing/invalid token or API key"}},
)
def sync_shared_vault_keys(tenant_id: uuid.UUID, apply: bool = False, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        return vault_service.sync_service_common_derived_keys(tenant.slug, apply=apply)
    except vault_service.VaultServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post(
    "/{tenant_id}/vault/prune-chart-provided-keys",
    summary="Remove TENANT_ID, VIRTUAL_HOST, REDIS_HOST and REDIS_PORT from a tenant's Vault secrets",
    description=(
        "For a tenant created before helm-chart-bridge set these four keys in every service's env itself: removes "
        "them from each of the tenant's Vault paths (`REDIS_PASSWORD` is not touched; a path that would be left with "
        "no key is skipped). **Dry run by default**; pass `apply=true` to rewrite the secrets. The chart's value wins "
        "over Vault, so nothing changes once the chart is deployed -- but any key whose Vault value differs from the "
        "chart's is listed under `differs` (an older tenant's `TENANT_ID` may still be the slug, and the pods then "
        "move to the bare tenant name), so check that first. Deploy the chart change before applying. Afterwards the "
        "chart re-syncs and the tenant's pods must be restarted."
    ),
    responses={404: {"description": "Tenant not found"}, 503: {"description": "Vault disabled or unreachable"},
               401: {"description": "Missing/invalid token or API key"}},
)
def prune_chart_provided_vault_keys(tenant_id: uuid.UUID, apply: bool = False, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        return vault_service.prune_chart_provided_keys(tenant.slug, tenant.tenant_name, tenant.domain or "", apply=apply)
    except vault_service.VaultServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


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
    description="Change version, user count, database size, the enabled-service selection or the chart release (`chartVersion`, a tag like `v0.1.1`). Only allowed while the tenant is `RUNNING` or `FAILED`. A tenant keeps the chart release it was created on until an update passes a new `chartVersion`. Returns the current record immediately; the change is applied in the background (status goes to `UPDATING`).",
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
    if tenant.helm_chart_version is None and req.chartVersion is None:
        # An update re-renders the tenant's values file from our record. With no pinned chart
        # version the file would carry none, and the tenant would move onto whatever chart the
        # ApplicationSet falls back to. Make the caller choose the version explicitly.
        raise HTTPException(
            status_code=409,
            detail="this tenant has no pinned chart version, so an update could move it onto a different "
                   "chart. Pin it first by sending chartVersion (the release it is running now) in this request.",
        )
    if req.chartVersion:
        try:
            chart_versions.check_exists(req.chartVersion)
        except chart_versions.ChartVersionError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
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
        req.chartVersion,
    )
    return tenant


_PURGE_STATUS = {"disabled": 403, "environment": 403, "confirm": 400, "state": 409, "name": 400, "namesake": 409}


def _purge_http_error(e: purge_service.PurgeNotAllowed) -> HTTPException:
    return HTTPException(status_code=_PURGE_STATUS.get(e.code, 400), detail=e.message)


@router.post(
    "/{tenant_id}/archive",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Archive a tenant",
    description=(
        "Stops the tenant without deleting anything: every service is scaled to zero and the ingress is removed, "
        "while the namespace, volumes, SQL and Mongo databases, Vault secrets and registry entries are kept. "
        "Status goes to `ARCHIVING`, then `ARCHIVED`. Undo it with `POST /tenant/{id}/restore`. Only a `RUNNING` "
        "tenant on a chart release that supports archiving (`ARCHIVE_MIN_CHART_VERSION` or later) can be archived."
    ),
    responses={404: {"description": "Tenant not found"}, 409: {"description": "Tenant is not RUNNING, or its chart release is too old"},
               401: {"description": "Missing/invalid token or API key"}},
)
def archive_tenant(tenant_id: uuid.UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status != TenantStatus.RUNNING:
        raise HTTPException(status_code=409, detail=f"tenant is currently {tenant.status}; only a RUNNING tenant can be archived")
    if not helm_values.supports_archive(tenant.helm_chart_version, settings.archive_min_chart_version):
        raise HTTPException(
            status_code=409,
            detail=f"this tenant is on chart {tenant.helm_chart_version or '(not pinned)'}, which does not support archiving "
                   f"(needs {settings.archive_min_chart_version} or later). Update it with PUT /tenant/{{id}} and a newer "
                   "chartVersion first; archiving it now would report success while its pods kept running.",
        )
    background_tasks.add_task(provisioner.archive_tenant, tenant.id)
    return {"tenantId": tenant.id, "status": "ARCHIVING"}


@router.post(
    "/{tenant_id}/restore",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore an archived tenant",
    description=(
        "Brings an `ARCHIVED` tenant back: its values file is committed without `archived`, Argo CD starts the "
        "services again and the status goes to `RESTORING`, then `RUNNING`. Its data was never touched, so nothing "
        "is re-seeded."
    ),
    responses={404: {"description": "Tenant not found"}, 409: {"description": "Tenant is not ARCHIVED"},
               401: {"description": "Missing/invalid token or API key"}},
)
def restore_tenant(tenant_id: uuid.UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status != TenantStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail=f"tenant is currently {tenant.status}; only an ARCHIVED tenant can be restored")
    background_tasks.add_task(provisioner.restore_tenant, tenant.id)
    return {"tenantId": tenant.id, "status": "RESTORING"}


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete a tenant",
    description=(
        "Starts teardown in the background: removes the tenant's manifest from git, lets Argo CD prune it and deletes the namespace. "
        "Status goes to `DELETING`, then `DELETED` (the row is soft-deleted). **Destructive.** "
        "A `RUNNING` tenant must be archived first (`POST /tenant/{id}/archive`); pass `force=true` to skip that.\n\n"
        "By default the tenant's data is kept: the SQL database and login, the Mongo databases, the Vault secrets and the onboarding-server record "
        "stay in place, and creating a tenant with the same name is refused until they are removed.\n\n"
        "`purge=true` removes that data too, once the tenant is `DELETED`. It is off unless the operator has `PURGE_ENABLED=true`, only works "
        "for the environments in `PURGE_ALLOWED_ENVIRONMENTS`, and needs `confirm` set to the tenant name. **Irreversible.** "
        "See `POST /tenant/{id}/purge` to preview it with `dry_run=true`, or to purge a tenant that is already deleted."
    ),
    responses={
        400: {"description": "`confirm` does not match the tenant name"},
        403: {"description": "`purge=true` but purging is disabled, or not allowed for this tenant's environment"},
        404: {"description": "Tenant not found"},
        409: {"description": "Deletion already in progress, the tenant is mid archive/restore, or it is RUNNING and was not archived"},
        **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}},
    },
)
def delete_tenant(
    tenant_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="Delete a RUNNING tenant directly instead of archiving it first"),
    purge: bool = Query(False, description="Also remove the tenant's SQL database/login, Mongo databases, Vault secrets and onboarding record"),
    confirm: str | None = Query(None, description="Required with purge=true: must equal the tenant name"),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    if tenant.status == TenantStatus.DELETING:
        raise HTTPException(status_code=409, detail="deletion already in progress")
    if tenant.status in (TenantStatus.ARCHIVING, TenantStatus.RESTORING):
        raise HTTPException(status_code=409, detail=f"tenant is currently {tenant.status}; wait for it to finish")
    if tenant.status == TenantStatus.RUNNING and not force:
        raise HTTPException(
            status_code=409,
            detail="this tenant is RUNNING. Archive it first (POST /tenant/{id}/archive), then delete the archived "
                   "tenant; or pass force=true to delete it directly.",
        )
    if purge:
        # Refuse before anything is deleted, so a rejected purge leaves the tenant untouched.
        try:
            purge_service.check_purge_allowed(tenant, confirm)
        except purge_service.PurgeNotAllowed as e:
            raise _purge_http_error(e) from e

    background_tasks.add_task(provisioner.delete_tenant, tenant.id, purge)
    return {"tenantId": tenant.id, "status": "DELETING", "purge": purge}


@router.post(
    "/{tenant_id}/purge",
    summary="Purge a deleted tenant's data (or preview it)",
    description=(
        "Removes the data a plain delete leaves behind, for a tenant that is already `DELETED`: the onboarding-server record, "
        "the Mongo databases, the SQL database and login, and the Vault secrets. The Postgres row, the registry record and the "
        "tenant-info file in git are kept as the audit trail.\n\n"
        "`dry_run=true` only reports what exists and what would be removed, and answers with the steps; it works for an `ARCHIVED`, `FAILED` or `DELETED` tenant, and says whether a real purge would be accepted (`purgeAllowed`, `purgeBlockedReason`). Without it, the purge runs in the "
        "background (202) and each step is recorded in the tenant's progress events; it needs `PURGE_ENABLED=true`, an allowed environment, "
        "and `confirm` equal to the tenant name. Safe to repeat. Refused while another tenant with the same name exists. **Irreversible.**"
    ),
    responses={
        400: {"description": "`confirm` does not match the tenant name, or the name is not purgeable"},
        403: {"description": "Purging is disabled, or not allowed for this tenant's environment"},
        404: {"description": "Tenant not found"},
        409: {"description": "The tenant is not DELETED (a dry run: not ARCHIVED, FAILED or DELETED), a live tenant has the same name, or a purge is already running"},
        **{401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}},
    },
)
def purge_tenant(
    tenant_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(False, description="Only report what would be removed"),
    confirm: str | None = Query(None, description="Required unless dry_run=true: must equal the tenant name"),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        if not dry_run:
            purge_service.check_purge_allowed(tenant, confirm)
        if dry_run:
            purge_service.check_previewable(db, tenant)
        else:
            purge_service.check_purgeable(db, tenant)
    except purge_service.PurgeNotAllowed as e:
        raise _purge_http_error(e) from e

    if dry_run:
        # Read-only, so it answers directly (the SQL check can take a little while). It also says
        # whether a real purge would be accepted, so a UI can offer it only when it can work.
        steps = purge_service.purge_tenant_data(tenant, dry_run=True)
        try:
            purge_service.check_purge_allowed(tenant, tenant.tenant_name)
            allowed, blocked = True, None
        except purge_service.PurgeNotAllowed as e:
            allowed, blocked = False, e.message
        return {"tenantId": tenant.id, "tenantName": tenant.tenant_name, "dryRun": True, "steps": steps,
                "purgeAllowed": allowed, "purgeBlockedReason": blocked}

    if purge_service.is_running(tenant.id):
        raise HTTPException(status_code=409, detail="a purge is already running for this tenant")
    background_tasks.add_task(provisioner.purge_tenant, tenant.id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"tenantId": str(tenant.id), "tenantName": tenant.tenant_name, "dryRun": False, "status": "PURGING"},
    )
