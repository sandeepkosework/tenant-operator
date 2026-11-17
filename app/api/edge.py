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
    out = {
        "have": have,
        "want": want,
        "missing": sorted(set(want) - have),     # a tenant with no route: its URL does not work
        "orphans": sorted(have - set(want)),     # a route with no live tenant behind it
        "problems": problems,
        "dns": None,
    }
    if edge_service.dns_enabled():
        # A DNS problem (say, GoDaddy refusing the key) must not hide the route report, so it is reported beside it.
        try:
            have_dns = set(edge_service.list_dns())
            out["dns"] = {"missing": sorted(set(want) - have_dns), "orphans": sorted(have_dns - set(want)), "error": None}
        except edge_service.EdgeError as e:
            out["dns"] = {"missing": [], "orphans": [], "error": str(e)}
    return out


@router.get("/routes", summary="Edge routes and DNS records vs. tenants", responses=_AUTH)
def list_routes(db: Session = Depends(get_db)):
    """Compares the route files on the edge host (and, when EDGE_DNS_ENABLED, the GoDaddy A records) with the
    tenants that should have them. Read-only: nothing is changed. `missing` are tenants whose URL does not work
    yet; `orphans` are files with no live tenant (a deleted tenant, or a hand-made file). `dns` is null when DNS
    automation is off; its `orphans` are tenant-style records pointing at the edge address that belong to no
    live tenant."""
    _require_enabled()
    c = _compare(db)
    return {"routes": sorted(c["have"]), "missing": c["missing"], "orphans": c["orphans"], "problems": c["problems"], "dns": c["dns"]}


@router.post("/sync", summary="Repair the edge routes and DNS records", responses=_AUTH)
def sync_routes(
    prune: bool = Query(False, description="Also delete routes that belong to no live tenant."),
    rewrite: bool = Query(False, description="Re-send every tenant's route and DNS record, to pick up a changed spoke upstream."),
    pruneDns: bool = Query(False, description="Also delete DNS records that point at the edge address but belong to no live tenant."),
    db: Session = Depends(get_db),
):
    """Adds the routes (and DNS records) tenants are missing -- after rebuilding the edge host, or for tenants
    created before this was switched on. `prune=true` also removes orphan routes; `rewrite=true` re-sends everything;
    `pruneDns=true` removes orphan DNS records (separate on purpose: a record pointing at the edge host is not
    necessarily a tenant's). Safe to run any time: each operation is idempotent."""
    _require_enabled()
    c = _compare(db)
    out: dict = {
        "added": [], "updated": [], "unchanged": [], "removed": [],
        "dnsAdded": [], "dnsUpdated": [], "dnsUnchanged": [], "dnsRemoved": [],
        "failed": [], "problems": c["problems"],
    }

    targets = sorted(c["want"]) if rewrite else c["missing"]
    for host in targets:
        try:
            result = edge_service.ensure_route(host, c["want"][host])
        except edge_service.EdgeError as e:
            out["failed"].append({"what": "route", "host": host, "error": str(e)})
            continue
        out["added" if result == "added" else "updated" if result == "updated" else "unchanged"].append(host)

    if prune:
        for host in c["orphans"]:
            try:
                edge_service.remove_route(host)
                out["removed"].append(host)
            except edge_service.EdgeError as e:
                out["failed"].append({"what": "route", "host": host, "error": str(e)})

    if c["dns"] is not None:
        if c["dns"]["error"]:
            out["failed"].append({"what": "dns", "host": None, "error": c["dns"]["error"]})
        else:
            for host in (sorted(c["want"]) if rewrite else c["dns"]["missing"]):
                try:
                    result = edge_service.ensure_dns(host)
                except edge_service.EdgeError as e:
                    out["failed"].append({"what": "dns", "host": host, "error": str(e)})
                    continue
                out["dnsAdded" if result == "added" else "dnsUpdated" if result == "updated" else "dnsUnchanged"].append(host)
            if pruneDns:
                for host in c["dns"]["orphans"]:
                    try:
                        edge_service.remove_dns(host)
                        out["dnsRemoved"].append(host)
                    except edge_service.EdgeError as e:
                        out["failed"].append({"what": "dns", "host": host, "error": str(e)})
    return out
