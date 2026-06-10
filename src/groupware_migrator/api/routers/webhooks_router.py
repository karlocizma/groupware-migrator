"""Webhooks API: manage webhook endpoints and view delivery history."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from groupware_migrator.api.auth import require_user
from groupware_migrator.engine.state import SQLiteStateStore

_VALID_EVENTS = frozenset(["job.completed", "job.failed", "job.cancelled"])


class CreateWebhookPayload(BaseModel):
    label: str = ""
    url: str
    events: list[str] | None = None


def _mask_secret(hook: dict) -> dict:
    out = dict(hook)
    if "secret" in out:
        out["secret"] = out["secret"][:6] + "***"
    return out


def create_webhooks_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["webhooks"])

    @router.post("")
    def create_webhook(
        payload: CreateWebhookPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        if not payload.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
        events = payload.events or ["job.completed", "job.failed"]
        invalid = set(events) - _VALID_EVENTS
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown event types: {sorted(invalid)}")
        secret = secrets.token_hex(32)
        user_id = str(current_user["sub"])
        webhook_id = state_store.create_webhook(
            user_id=user_id,
            label=payload.label,
            url=payload.url,
            secret=secret,
            events=events,
        )
        hook = state_store.get_webhook(webhook_id)
        # Return full secret only on creation
        return {**hook, "secret": secret}

    @router.get("")
    def list_webhooks(current_user: dict = Depends(require_user)) -> dict:
        is_admin = current_user.get("is_admin")
        user_id = None if is_admin else str(current_user["sub"])
        hooks = state_store.list_webhooks(user_id=user_id)
        return {"items": [_mask_secret(h) for h in hooks]}

    @router.get("/{webhook_id}")
    def get_webhook(
        webhook_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        hook = state_store.get_webhook(webhook_id)
        if not hook:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        if not current_user.get("is_admin") and hook.get("user_id") != str(current_user["sub"]):
            raise HTTPException(status_code=403, detail="Access denied.")
        return _mask_secret(hook)

    @router.delete("/{webhook_id}")
    def delete_webhook(
        webhook_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        hook = state_store.get_webhook(webhook_id)
        if not hook:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        if not current_user.get("is_admin") and hook.get("user_id") != str(current_user["sub"]):
            raise HTTPException(status_code=403, detail="Access denied.")
        state_store.delete_webhook(webhook_id)
        return {"ok": True}

    @router.get("/{webhook_id}/deliveries")
    def list_deliveries(
        webhook_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        hook = state_store.get_webhook(webhook_id)
        if not hook:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        if not current_user.get("is_admin") and hook.get("user_id") != str(current_user["sub"]):
            raise HTTPException(status_code=403, detail="Access denied.")
        deliveries = state_store.list_webhook_deliveries(webhook_id)
        return {"items": deliveries}

    return router
