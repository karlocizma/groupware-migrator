"""Scheduler API: CRUD for cron/interval-based recurring migration jobs."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from groupware_migrator.api.auth import require_admin, require_user
from groupware_migrator.engine.scheduler import compute_next_run
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import MigrationRequest


class CreateSchedulePayload(BaseModel):
    name: str = ""
    schedule_type: str = "cron"      # "cron" | "interval"
    schedule_expr: str               # e.g. "0 2 * * *" or "6h"
    request: dict                    # full MigrationRequest dict (credentials included)


class UpdateSchedulePayload(BaseModel):
    name: str | None = None
    schedule_expr: str | None = None
    is_active: bool | None = None


def _schedule_out(row: dict) -> dict:
    out = dict(row)
    out.pop("request_json", None)  # don't leak credentials
    return out


def create_scheduler_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter(prefix="/schedules", tags=["schedules"])

    @router.post("")
    def create_schedule(
        payload: CreateSchedulePayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        valid_types = {"cron", "interval"}
        if payload.schedule_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"schedule_type must be one of {valid_types}")
        # Validate the request can be parsed
        try:
            _req = MigrationRequest.from_dict(payload.request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid request: {exc}") from exc
        # Validate the schedule expression
        try:
            next_run = compute_next_run(payload.schedule_type, payload.schedule_expr)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid schedule expression: {exc}") from exc

        user_id = str(current_user["sub"])
        request_json = json.dumps(payload.request, sort_keys=True)
        schedule_id = state_store.create_schedule(
            name=payload.name,
            schedule_type=payload.schedule_type,
            schedule_expr=payload.schedule_expr,
            request_json=request_json,
            next_run_at=next_run,
            user_id=user_id,
        )
        row = state_store.get_schedule(schedule_id)
        return _schedule_out(row)

    @router.get("")
    def list_schedules(current_user: dict = Depends(require_user)) -> dict:
        is_admin = current_user.get("is_admin")
        user_id = None if is_admin else str(current_user["sub"])
        rows = state_store.list_schedules(user_id=user_id)
        return {"items": [_schedule_out(r) for r in rows]}

    @router.get("/{schedule_id}")
    def get_schedule(
        schedule_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        row = state_store.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        if not current_user.get("is_admin") and row.get("user_id") != str(current_user["sub"]):
            raise HTTPException(status_code=403, detail="Access denied.")
        return _schedule_out(row)

    @router.patch("/{schedule_id}")
    def update_schedule(
        schedule_id: str,
        payload: UpdateSchedulePayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        row = state_store.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        if not current_user.get("is_admin") and row.get("user_id") != str(current_user["sub"]):
            raise HTTPException(status_code=403, detail="Access denied.")

        next_run_at = None
        if payload.schedule_expr is not None:
            try:
                next_run_at = compute_next_run(str(row["schedule_type"]), payload.schedule_expr)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid schedule expression: {exc}") from exc

        state_store.update_schedule(
            schedule_id,
            name=payload.name,
            schedule_expr=payload.schedule_expr,
            is_active=payload.is_active,
            next_run_at=next_run_at,
        )
        updated = state_store.get_schedule(schedule_id)
        return _schedule_out(updated)

    @router.delete("/{schedule_id}")
    def delete_schedule(
        schedule_id: str,
        current_user: dict = Depends(require_user),
    ) -> dict:
        row = state_store.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        if not current_user.get("is_admin") and row.get("user_id") != str(current_user["sub"]):
            raise HTTPException(status_code=403, detail="Access denied.")
        state_store.delete_schedule(schedule_id)
        return {"ok": True}

    return router
