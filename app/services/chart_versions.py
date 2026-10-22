"""
Which chart release a tenant is created on.

A new tenant gets the newest release tag (vN.N.N) of the chart repository unless the request
names one. A named version must exist as a tag, so a typo is rejected up front instead of
surfacing later as an Argo CD error. Existing tenants are never touched here: they only move
through an update that passes a new chartVersion.
"""
import logging

from app.config import get_settings
from app.services import git_service

logger = logging.getLogger("tenant-operator.chart_versions")
settings = get_settings()


class ChartVersionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _sort_key(version: str) -> tuple:
    return tuple(int(part) for part in version[1:].split("."))


def available() -> list[str] | None:
    """Release tags, oldest first; None when the chart repository cannot be read."""
    try:
        return sorted(git_service.list_remote_tags(), key=_sort_key)
    except git_service.GitServiceError as e:
        logger.warning("%s", e)
        return None


def resolve_for_create(requested: str | None) -> str:
    """The chart version a new tenant is pinned to.

    - A version in the request must exist (503 if that cannot be checked right now).
    - Otherwise the newest release; if the repository cannot be read, has no release tag, or
      helm_chart_use_latest is off, the configured helm_chart_version.
    """
    tags = available()
    if requested:
        if tags is None:
            raise ChartVersionError(503, f"could not check that chart version {requested} exists (the chart "
                                         "repository is unreachable), so nothing was created. Try again.")
        if requested not in tags:
            raise ChartVersionError(400, _unknown(requested, tags))
        return requested

    if not settings.helm_chart_use_latest:
        return settings.helm_chart_version
    if tags:
        return tags[-1]
    logger.warning("no chart release tag could be read; creating the tenant on the configured default %s",
                   settings.helm_chart_version)
    return settings.helm_chart_version


def check_exists(version: str) -> None:
    """Rejects a version that is not a tag of the chart repository. When the repository cannot be
    read the check is skipped (logged): Argo CD still reports a bad tag on that tenant."""
    tags = available()
    if tags is not None and version not in tags:
        raise ChartVersionError(400, _unknown(version, tags))


def _unknown(version: str, tags: list[str]) -> str:
    return f"chart version {version} is not a release of the chart repository (available: {', '.join(tags) or 'none'})"
