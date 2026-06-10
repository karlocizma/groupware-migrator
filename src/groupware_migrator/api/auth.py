from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from groupware_migrator.engine.state import SQLiteStateStore

COOKIE_NAME = "gm_session"
JWT_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def _jwt_secret(request: Request) -> str:
    return str(request.app.state.jwt_secret)


def create_access_token(payload: dict, *, secret: str, ttl_hours: int = 8) -> str:
    data = {**payload, "exp": datetime.now(timezone.utc) + timedelta(hours=ttl_hours)}
    return jwt.encode(data, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    secret = _jwt_secret(request)
    state_store: SQLiteStateStore = request.app.state.state_store

    token = request.cookies.get(COOKIE_NAME)
    if token:
        payload = decode_access_token(token, secret=secret)
        if payload:
            return payload

    if credentials:
        user = state_store.validate_api_key(credentials.credentials)
        if user:
            return user

    return None


def require_user(current_user: dict | None = Depends(get_current_user)) -> dict:
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def require_admin(current_user: dict = Depends(require_user)) -> dict:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return current_user
