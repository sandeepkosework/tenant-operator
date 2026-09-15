from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Database = Depends(get_db)):
    db.command("ping")
    return {"status": "ok"}
