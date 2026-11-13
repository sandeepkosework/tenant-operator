from fastapi import APIRouter

from app.config import get_settings
from app.services import chart_versions
from app.services.helm_values import QRAIE_BRIDGE_SERVICE_NAMES

router = APIRouter(prefix="/meta", tags=["meta"])
settings = get_settings()


@router.get(
    "/create-options",
    summary="What the create-tenant form can offer",
    description=(
        "The service names a tenant can enable or disable, the base domain tenant hostnames are built under, "
        "and the chart releases a new tenant can be created on. `chartVersions` is null when the chart "
        "repository cannot be read right now; the form then just leaves the version to the server's default."
    ),
    responses={401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}},
)
def create_options():
    return {
        "services": sorted(QRAIE_BRIDGE_SERVICE_NAMES),
        "baseDomain": settings.bridge_base_domain,
        "environment": settings.environment,
        "chartVersions": chart_versions.available(),
        "defaultChartVersion": settings.helm_chart_version,
    }
