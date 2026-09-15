from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from app.database import get_db
from app.services import cluster_selector, spoke_cr

router = APIRouter(prefix="/cluster", tags=["cluster"])


@router.get("")
def list_clusters(db: Database = Depends(get_db)):
    """Current SpokeCluster CR content for every registered spoke -- tenant list + count per environment."""
    return [
        spoke_cr.get_spoke_cluster_status(c, db)
        for c in cluster_selector.load_cluster_registry()
    ]


@router.get("/{name}")
def get_cluster(name: str, db: Database = Depends(get_db)):
    cluster = next((c for c in cluster_selector.load_cluster_registry() if c.name == name), None)
    if cluster is None:
        raise HTTPException(status_code=404, detail=f"cluster '{name}' not found in registry")
    return spoke_cr.get_spoke_cluster_status(cluster, db)
