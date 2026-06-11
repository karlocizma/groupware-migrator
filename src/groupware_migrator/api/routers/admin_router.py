from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from groupware_migrator.api.auth import require_admin
from groupware_migrator.engine.state import SQLiteStateStore, hash_password

if TYPE_CHECKING:
    from groupware_migrator.engine.mailer import MailDeliveryManager


class UpdateUserPayload(BaseModel):
    is_admin: bool | None = None
    is_active: bool | None = None


class ChangePasswordAdminPayload(BaseModel):
    new_password: str


class CleanupPayload(BaseModel):
    older_than_days: int = 90


def create_admin_router(
    state_store: SQLiteStateStore,
    mail_manager: "MailDeliveryManager | None" = None,
) -> APIRouter:
    router = APIRouter(prefix="/admin")

    @router.get("/stats")
    def get_stats(_admin: dict = Depends(require_admin)) -> dict:
        return state_store.system_stats()

    @router.get("/users")
    def list_users(_admin: dict = Depends(require_admin)) -> dict:
        users = state_store.list_users()
        return {"items": users}

    @router.patch("/users/{user_id}")
    def update_user(
        user_id: str,
        payload: UpdateUserPayload,
        admin: dict = Depends(require_admin),
    ) -> dict:
        if payload.is_admin is None and payload.is_active is None:
            raise HTTPException(status_code=400, detail="Nothing to update.")
        updated = state_store.update_user(
            user_id, is_admin=payload.is_admin, is_active=payload.is_active
        )
        if not updated:
            raise HTTPException(status_code=404, detail="User not found.")
        state_store.log_admin_action(
            admin_id=str(admin["sub"]),
            action="update_user",
            target_id=user_id,
            details=payload.model_dump(exclude_none=True),
        )
        return {"ok": True}

    @router.post("/users/{user_id}/reset-password")
    def admin_reset_password(
        user_id: str,
        payload: ChangePasswordAdminPayload,
        admin: dict = Depends(require_admin),
    ) -> dict:
        if not payload.new_password or len(payload.new_password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
        updated = state_store.change_password(user_id, hash_password(payload.new_password))
        if not updated:
            raise HTTPException(status_code=404, detail="User not found.")
        state_store.log_admin_action(
            admin_id=str(admin["sub"]),
            action="reset_password",
            target_id=user_id,
        )
        return {"ok": True}

    @router.get("/audit-log")
    def get_audit_log(
        limit: int = Query(default=100, ge=1, le=500),
        _admin: dict = Depends(require_admin),
    ) -> dict:
        return {"items": state_store.list_admin_audit_events(limit=limit)}

    @router.post("/cleanup")
    def cleanup_records(
        payload: CleanupPayload,
        admin: dict = Depends(require_admin),
    ) -> dict:
        if payload.older_than_days < 1:
            raise HTTPException(status_code=400, detail="older_than_days must be at least 1.")
        result = state_store.cleanup_old_records(older_than_days=payload.older_than_days)
        state_store.log_admin_action(
            admin_id=str(admin["sub"]),
            action="cleanup_records",
            details={"older_than_days": payload.older_than_days, **result},
        )
        return result

    @router.post("/smtp/test")
    def smtp_test(admin: dict = Depends(require_admin)) -> dict:
        if mail_manager is None or not mail_manager.is_configured():
            raise HTTPException(
                status_code=503,
                detail="SMTP is not configured (SMTP_HOST not set).",
            )
        user = state_store.get_user_by_id(str(admin["sub"]))
        to_address = str(user["email"]) if user and user.get("email") else ""
        if not to_address:
            raise HTTPException(status_code=400, detail="Admin account has no email address.")
        try:
            mail_manager.send_test(to_address=to_address)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"SMTP error: {exc}") from exc
        return {"ok": True, "sent_to": to_address}

    @router.get("/ldap/status")
    def ldap_status(_admin: dict = Depends(require_admin)) -> dict:
        host = os.environ.get("LDAP_HOST", "")
        return {"configured": bool(host), "host": host or None}

    return router
