"""Organizations API: create and manage multi-tenant workspaces."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from groupware_migrator.api.auth import require_admin, require_user
from groupware_migrator.engine.state import SQLiteStateStore


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:48] or "org"


class CreateOrgPayload(BaseModel):
    name: str


class AddMemberPayload(BaseModel):
    user_id: str
    role: str = "member"


def create_orgs_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter(prefix="/orgs", tags=["organizations"])

    @router.post("")
    def create_org(
        payload: CreateOrgPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="Organization name cannot be empty.")
        slug = _slugify(payload.name)
        user_id = str(current_user["sub"])
        # Ensure slug uniqueness (append random suffix if taken)
        if state_store.get_org_by_slug(slug):
            import secrets as _s  # noqa: PLC0415
            slug = slug[:40] + "-" + _s.token_hex(3)
        try:
            org_id = state_store.create_org(name=payload.name.strip(), slug=slug, created_by=user_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return state_store.get_org(org_id)

    @router.get("")
    def list_orgs(current_user: dict = Depends(require_user)) -> dict:
        is_admin = current_user.get("is_admin")
        user_id = None if is_admin else str(current_user["sub"])
        orgs = state_store.list_orgs(user_id=user_id)
        return {"items": orgs}

    @router.get("/{org_id}")
    def get_org(
        org_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        org = state_store.get_org(org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found.")
        user_id = str(current_user["sub"])
        if not current_user.get("is_admin"):
            role = state_store.get_org_member_role(org_id, user_id)
            if not role:
                raise HTTPException(status_code=403, detail="Access denied.")
        return org

    @router.get("/{org_id}/members")
    def list_members(
        org_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        org = state_store.get_org(org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found.")
        user_id = str(current_user["sub"])
        if not current_user.get("is_admin"):
            role = state_store.get_org_member_role(org_id, user_id)
            if not role:
                raise HTTPException(status_code=403, detail="Access denied.")
        members = state_store.list_org_members(org_id)
        return {"items": members}

    @router.post("/{org_id}/members")
    def add_member(
        org_id: str,
        payload: AddMemberPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        org = state_store.get_org(org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found.")
        user_id = str(current_user["sub"])
        if not current_user.get("is_admin"):
            caller_role = state_store.get_org_member_role(org_id, user_id)
            if caller_role not in ("owner", "admin"):
                raise HTTPException(status_code=403, detail="Only org owners/admins can add members.")
        valid_roles = {"owner", "admin", "member"}
        if payload.role not in valid_roles:
            raise HTTPException(status_code=400, detail=f"Role must be one of {sorted(valid_roles)}")
        # Verify target user exists
        target_user = state_store.get_user_by_id(payload.user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found.")
        state_store.add_org_member(org_id, payload.user_id, payload.role)
        return {"ok": True, "org_id": org_id, "user_id": payload.user_id, "role": payload.role}

    @router.delete("/{org_id}/members/{user_id}")
    def remove_member(
        org_id: str,
        user_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        org = state_store.get_org(org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found.")
        caller_id = str(current_user["sub"])
        if not current_user.get("is_admin"):
            caller_role = state_store.get_org_member_role(org_id, caller_id)
            if caller_role not in ("owner", "admin") and caller_id != user_id:
                raise HTTPException(status_code=403, detail="Only org owners/admins can remove members.")
        removed = state_store.remove_org_member(org_id, user_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Member not found.")
        return {"ok": True}

    @router.delete("/{org_id}")
    def delete_org(
        org_id: str,
        current_user: dict = Depends(require_admin),
    ) -> dict:
        org = state_store.get_org(org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found.")
        state_store.delete_org(org_id)
        return {"ok": True}

    return router
