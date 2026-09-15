from fastapi import APIRouter, HTTPException

from app.services import vault_service

router = APIRouter(prefix="/vault", tags=["vault"])


@router.get("/qraie-bridge-defaults")
def get_qraie_bridge_platform_defaults():
    """Current shared (non-tenant-specific) config for every qraie-bridge
    service -- external API creds, business URLs, etc. that are the same
    for every tenant in this environment. Falls back to all-empty-string
    per service until set here. See vault_service.QRAIE_BRIDGE_SERVICE_KEYS
    for the full schema."""
    return vault_service.read_qraie_bridge_platform_defaults()


@router.put("/qraie-bridge-defaults/{service}")
def put_qraie_bridge_platform_defaults(service: str, config: dict):
    """Overwrites the shared config for one qraie-bridge service, e.g.
    PUT .../tranops-backend {"SLM_API_URL": "...", "SLM_PASSWORD": "..."}.
    Only affects tenants provisioned AFTER this call -- existing tenants'
    secret/tenants/<slug>/<service> paths are written once at creation, not
    kept in sync with this."""
    try:
        vault_service.write_qraie_bridge_platform_defaults(service, config)
    except vault_service.VaultServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "service": service, "keys": list(config.keys())}
