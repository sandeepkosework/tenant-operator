"""Minimal admin auth for the built-in web UI: a stateless HMAC-signed bearer
token (stdlib only, so it works across replicas with no session store)."""
import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings


def _secret() -> bytes:
    s = get_settings()
    return (s.admin_token_secret or s.admin_password or "").encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def create_token(username: str) -> str:
    ttl = get_settings().admin_token_ttl_seconds
    payload = _b64(json.dumps({"u": username, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str) -> bool:
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data["exp"] > time.time()
    except Exception:
        return False


_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/v1/auth/login")
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="Static API key (API_KEY in tenant-operator-secrets)")


def api_key_valid(key: Optional[str]) -> bool:
    configured = get_settings().api_key
    return bool(configured and key and hmac.compare_digest(key, configured))


def require_admin(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    x_api_key: Optional[str] = Depends(_api_key),
) -> None:
    """Guards every API route except /health and /auth/login. Accepts either
    the login bearer token or the static X-API-Key. Fails closed: with
    neither ADMIN_PASSWORD nor API_KEY set, nothing is reachable."""
    s = get_settings()
    if not s.admin_password and not s.api_key:
        raise HTTPException(status_code=503, detail="auth not configured (set ADMIN_PASSWORD and/or API_KEY)")
    if api_key_valid(x_api_key):
        return
    if creds is not None and s.admin_password and verify_token(creds.credentials):
        return
    raise HTTPException(status_code=401, detail="login or X-API-Key required")
