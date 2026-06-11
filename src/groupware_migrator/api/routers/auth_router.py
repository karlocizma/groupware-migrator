from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from groupware_migrator.api.auth import (
    COOKIE_NAME,
    create_access_token,
    get_user_role,
    require_admin,
    require_user,
)
from groupware_migrator.api.rate_limit import LoginRateLimiter
from groupware_migrator.engine.ldap_auth import LDAPAuthBackend, LDAPAuthError
from groupware_migrator.engine.state import SQLiteStateStore, hash_password, verify_password

_login_limiter = LoginRateLimiter(max_attempts=5, window_seconds=300, lockout_seconds=900)


class LoginPayload(BaseModel):
    email: str
    password: str
    totp_code: str = ""


class CreateUserPayload(BaseModel):
    email: str
    password: str
    is_admin: bool = False
    role: str = "operator"


class CreateApiKeyPayload(BaseModel):
    label: str = ""


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


class TotpConfirmPayload(BaseModel):
    code: str


class TotpDisablePayload(BaseModel):
    current_password: str


class NotificationPrefsPayload(BaseModel):
    on_completed: bool
    on_failed: bool
    on_cancelled: bool


def _ttl_hours() -> int:
    return int(os.environ.get("JWT_TTL_HOURS", "8"))


def create_auth_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    def login(payload: LoginPayload, request: Request, response: Response) -> dict:
        import pyotp  # noqa: PLC0415

        client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        _login_limiter.check_and_record(client_ip)

        user = state_store.get_user_by_email(payload.email)
        _ldap = LDAPAuthBackend()

        if user and user.get("auth_backend", "local") == "ldap":
            # Existing LDAP user
            try:
                ldap_info = _ldap.authenticate(payload.email, payload.password)
            except LDAPAuthError as exc:
                raise HTTPException(status_code=503, detail=f"LDAP server unreachable: {exc}") from exc
            if ldap_info is None:
                raise HTTPException(status_code=401, detail="Invalid email or password.")

        elif user and user.get("auth_backend", "local") == "local":
            # Local user — bcrypt path
            if not verify_password(payload.password, str(user["password_hash"])):
                raise HTTPException(status_code=401, detail="Invalid email or password.")
            if not user.get("is_active", 1):
                raise HTTPException(status_code=403, detail="Account is deactivated.")
            # TOTP check
            if user.get("totp_enabled"):
                if not payload.totp_code:
                    return {"totp_required": True}
                totp = pyotp.TOTP(str(user["totp_secret"]))
                if not totp.verify(payload.totp_code, valid_window=1):
                    if not state_store.consume_totp_recovery_code(str(user["id"]), payload.totp_code):
                        raise HTTPException(status_code=401, detail="Invalid TOTP code.")

        elif not user and _ldap.is_configured():
            # First LDAP login — auto-provision
            try:
                ldap_info = _ldap.authenticate(payload.email, payload.password)
            except LDAPAuthError as exc:
                raise HTTPException(status_code=503, detail=f"LDAP server unreachable: {exc}") from exc
            if ldap_info is None:
                raise HTTPException(status_code=401, detail="Invalid email or password.")
            default_role = os.environ.get("LDAP_DEFAULT_ROLE", "operator")
            user_id = state_store.create_user(
                email=ldap_info["email"],
                password_hash="!",
                role=default_role,
                auth_backend="ldap",
            )
            user = state_store.get_user_by_id(user_id)

        else:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        if not user.get("is_active", 1):
            raise HTTPException(status_code=403, detail="Account is deactivated.")

        _login_limiter.clear(client_ip)
        role = get_user_role(user)
        token = create_access_token(
            {
                "sub": str(user["id"]),
                "email": str(user["email"]),
                "is_admin": bool(user["is_admin"]),
                "role": role,
            },
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
        return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"]), "role": role}

    @router.post("/auth/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @router.get("/auth/me")
    def me(current_user: dict = Depends(require_user)) -> dict:
        role = get_user_role(current_user)
        return {
            "id": current_user.get("sub"),
            "email": current_user.get("email"),
            "is_admin": bool(current_user.get("is_admin")),
            "role": role,
        }

    @router.post("/auth/users")
    def create_user(
        payload: CreateUserPayload,
        _admin: dict = Depends(require_admin),
    ) -> dict:
        valid_roles = {"viewer", "operator", "admin", "super_admin"}
        if payload.role not in valid_roles:
            raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(valid_roles)}")
        try:
            user_id = state_store.create_user(
                email=payload.email,
                password_hash=hash_password(payload.password),
                is_admin=payload.is_admin or payload.role in ("admin", "super_admin"),
                role=payload.role,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": user_id, "email": payload.email, "is_admin": payload.is_admin, "role": payload.role}

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
        _user_row = state_store.get_user_by_id(str(current_user["sub"]))
        if _user_row and _user_row.get("auth_backend") == "ldap":
            raise HTTPException(status_code=400, detail="Password change is not available for LDAP accounts.")
        if len(payload.new_password) < 8:
            raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
        user_id = str(current_user["sub"])
        user = state_store.get_user_by_id(user_id)
        if not user or not verify_password(payload.current_password, str(user["password_hash"])):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        state_store.change_password(user_id, hash_password(payload.new_password))
        return {"ok": True}

    @router.get("/auth/totp/setup")
    def totp_setup(current_user: dict = Depends(require_user)) -> dict:
        """Generate a new TOTP secret and recovery codes (not yet active until confirmed)."""
        import pyotp  # noqa: PLC0415

        _user_row = state_store.get_user_by_id(str(current_user["sub"]))
        if _user_row and _user_row.get("auth_backend") == "ldap":
            raise HTTPException(status_code=400, detail="TOTP is not available for LDAP accounts.")
        user_id = str(current_user["sub"])
        email = str(current_user.get("email", "user"))
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name=email, issuer_name="Groupware Migrator")
        recovery_codes = [secrets.token_hex(4).upper() for _ in range(10)]
        state_store.set_totp_secret(user_id, secret=secret, recovery_codes=recovery_codes)
        return {
            "secret": secret,
            "uri": uri,
            "recovery_codes": recovery_codes,
        }

    @router.post("/auth/totp/confirm")
    def totp_confirm(
        payload: TotpConfirmPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        """Verify the TOTP code and enable 2FA."""
        import pyotp  # noqa: PLC0415

        user_id = str(current_user["sub"])
        user = state_store.get_user_by_id(user_id)
        if not user or not user.get("totp_secret"):
            raise HTTPException(status_code=400, detail="No TOTP secret set. Call /auth/totp/setup first.")
        totp = pyotp.TOTP(str(user["totp_secret"]))
        if not totp.verify(payload.code, valid_window=1):
            raise HTTPException(status_code=400, detail="Invalid TOTP code.")
        if not state_store.enable_totp(user_id):
            raise HTTPException(status_code=500, detail="Failed to enable TOTP.")
        return {"ok": True, "totp_enabled": True}

    @router.post("/auth/totp/disable")
    def totp_disable(
        payload: TotpDisablePayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        """Disable TOTP (requires current password confirmation)."""
        user_id = str(current_user["sub"])
        user = state_store.get_user_by_id(user_id)
        if not user or not verify_password(payload.current_password, str(user["password_hash"])):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        state_store.disable_totp(user_id)
        return {"ok": True, "totp_enabled": False}

    @router.get("/auth/totp/status")
    def totp_status(current_user: dict = Depends(require_user)) -> dict:
        user_id = str(current_user["sub"])
        user = state_store.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        return {"totp_enabled": bool(user.get("totp_enabled", 0))}

    @router.get("/auth/notifications")
    def get_notification_prefs(current_user: dict = Depends(require_user)) -> dict:
        return state_store.get_notification_prefs(str(current_user["sub"]))

    @router.patch("/auth/notifications")
    def set_notification_prefs(
        payload: NotificationPrefsPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        state_store.set_notification_prefs(
            str(current_user["sub"]),
            on_completed=payload.on_completed,
            on_failed=payload.on_failed,
            on_cancelled=payload.on_cancelled,
        )
        return state_store.get_notification_prefs(str(current_user["sub"]))

    return router
