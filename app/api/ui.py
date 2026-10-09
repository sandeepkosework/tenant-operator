from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)
_PAGE = Path(__file__).resolve().parent.parent / "static" / "ui.html"


@router.get("/ui", response_class=HTMLResponse)
def ui():
    return _PAGE.read_text(encoding="utf-8")
