import hmac

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.auth import create_token
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post(
    "/login",
    summary="Log in and get a bearer token",
    description="Exchange the admin username/password for a short-lived bearer token (8h by default). Send it as `Authorization: Bearer <token>`, or click **Authorize** in these docs.",
    responses={401: {"description": "Invalid username or password"}, 503: {"description": "ADMIN_PASSWORD is not configured"}},
)
def login(req: LoginRequest):
    s = get_settings()
    if not s.admin_password:
        raise HTTPException(status_code=503, detail="admin login is not configured (set ADMIN_PASSWORD)")
    ok = hmac.compare_digest(req.username, s.admin_username) & hmac.compare_digest(req.password, s.admin_password)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"token": create_token(req.username)}
