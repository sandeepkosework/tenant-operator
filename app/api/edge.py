from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import cluster_selector, edge_service

router = APIRouter(prefix="/edge", tags=["edge"])

_AUTH = {401: {"description": "Missing/invalid token or API key"}, 503: {"description": "Auth not configured on the operator (no ADMIN_PASSWORD/API_KEY)"}}


def _require_enabled() -> None:
    if not edge_service.is_enabled():
        raise HTTPException(status_code=409, detail="edge routing is disabled (EDGE_ENABLED=false)")


def _compare(db: Session) -> dict:
    try:
        have = set(edge_service.list_routes())
    except edge_service.EdgeError as e:
        raise HTTPException(status_code=502, detail=f"edge host: {e}")
    want, problems = edge_service.desired_routes(db, cluster_selector.load_cluster_registry())
    return {
        "have": have,
        "want": want,
        "missing": sorted(set(want) - have),     # a tenant with no route: its URL does not work
        "orphans": sorted(have - set(want)),     # a route with no live tenant behind it
        "problems": problems,
    }


@router.get("/routes", summary="Edge routes vs. tenants", responses=_AUTH)
def list_routes(db: Session = Depends(get_db)):
    """Compares the route files on the edge host with the tenants that should have one. Read-only:
    nothing is changed. `missing` are tenants whose URL does not work yet; `orphans` are files with no
    live tenant (a deleted tenant, or a hand-made file)."""
    _require_enabled()
    c = _compare(db)
    return {"routes": sorted(c["have"]), "missing": c["missing"], "orphans": c["orphans"], "problems": c["problems"]}


@router.post("/sync", summary="Repair the edge routes", responses=_AUTH)
def sync_routes(
    prune: bool = Query(False, description="Also delete routes that belong to no live tenant."),
    rewrite: bool = Query(False, description="Re-send every tenant's route, to pick up a changed spoke upstream."),
    db: Session = Depends(get_db),
):
    """Adds the routes tenants are missing (after rebuilding the edge host, or for tenants created
    before edge routing was switched on). `prune=true` also removes orphan routes; `rewrite=true` re-sends
    every route. Safe to run any time: each operation is idempotent."""
    _require_enabled()
    c = _compare(db)
    out: dict = {"added": [], "updated": [], "unchanged": [], "removed": [], "failed": [], "problems": c["problems"]}

    targets = sorted(c["want"]) if rewrite else c["missing"]
    for host in targets:
        try:
            result = edge_service.ensure_route(host, c["want"][host])
        except edge_service.EdgeError as e:
            out["failed"].append({"host": host, "error": str(e)})
            continue
        out["added" if result == "added" else "updated" if result == "updated" else "unchanged"].append(host)

    if prune:
        for host in c["orphans"]:
            try:
                edge_service.remove_route(host)
                out["removed"].append(host)
            except edge_service.EdgeError as e:
                out["failed"].append({"host": host, "error": str(e)})
    return out
