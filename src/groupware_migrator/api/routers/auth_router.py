from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from groupware_migrator.api.auth import (
    COOKIE_NAME,
    create_access_token,
    require_admin,
    require_user,
)
from groupware_migrator.api.rate_limit import LoginRateLimiter
from groupware_migrator.engine.state import SQLiteStateStore, hash_password, verify_password

_login_limiter = LoginRateLimiter(max_attempts=5, window_seconds=300, lockout_seconds=900)


class LoginPayload(BaseModel):
    email: str
    password: str


class CreateUserPayload(BaseModel):
    email: str
    password: str
    is_admin: bool = False


class CreateApiKeyPayload(BaseModel):
    label: str = ""


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


def _ttl_hours() -> int:
    return int(os.environ.get("JWT_TTL_HOURS", "8"))


def create_auth_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    def login(payload: LoginPayload, request: Request, response: Response) -> dict:
        client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        _login_limiter.check_and_record(client_ip)
        user = state_store.get_user_by_email(payload.email)
        if not user or not verify_password(payload.password, str(user["password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if not user.get("is_active", 1):
            raise HTTPException(status_code=403, detail="Account is deactivated.")
        _login_limiter.clear(client_ip)
        token = create_access_token(
            {"sub": str(user["id"]), "email": str(user["email"]), "is_admin": bool(user["is_admin"])},
            secret=str(request.app.state.jwt_secret),
            ttl_hours=_ttl_hours(),
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
            max_age=_ttl_hours() * 3600,
        )
        return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"])}

    @router.post("/auth/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @router.get("/auth/me")
    def me(current_user: dict = Depends(require_user)) -> dict:
        return {
            "id": current_user.get("sub"),
            "email": current_user.get("email"),
            "is_admin": bool(current_user.get("is_admin")),
        }

    @router.post("/auth/users")
    def create_user(
        payload: CreateUserPayload,
        _admin: dict = Depends(require_admin),
    ) -> dict:
        try:
            user_id = state_store.create_user(
                email=payload.email,
                password_hash=hash_password(payload.password),
                is_admin=payload.is_admin,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": user_id, "email": payload.email, "is_admin": payload.is_admin}

    @router.get("/auth/users")
    def list_users(_admin: dict = Depends(require_admin)) -> dict:
        return {"items": state_store.list_users()}

    @router.post("/auth/keys")
    def create_api_key(
        payload: CreateApiKeyPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        user_id = str(current_user["sub"])
        key_id, raw_key = state_store.create_api_key(user_id=user_id, label=payload.label)
        return {"key_id": key_id, "key": raw_key, "label": payload.label}

    @router.get("/auth/keys")
    def list_api_keys(current_user: dict = Depends(require_user)) -> dict:
        user_id = str(current_user["sub"])
        return {"items": state_store.list_api_keys(user_id=user_id)}

    @router.delete("/auth/keys/{key_id}")
    def revoke_api_key(
        key_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        user_id = str(current_user["sub"])
        deleted = state_store.revoke_api_key(key_id=key_id, user_id=user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="API key not found.")
        return {"ok": True}

    @router.post("/auth/change-password")
    def change_password(
        payload: ChangePasswordPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        if len(payload.new_password) < 8:
            raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
        user_id = str(current_user["sub"])
        user = state_store.get_user_by_id(user_id)
        if not user or not verify_password(payload.current_password, str(user["password_hash"])):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        state_store.change_password(user_id, hash_password(payload.new_password))
        return {"ok": True}

    return router
