from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from groupware_migrator.api.schemas import BatchPayload, BatchPreflightPayload
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.batch import build_batch_preview, build_batch_rows
from groupware_migrator.engine.preflight import run_preflight
from groupware_migrator.engine.state import SQLiteStateStore, derive_batch_status
from groupware_migrator.models import JobStatus, MigrationPlan


def _sse_message(event: str, payload: dict) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


def _batch_item_response(item_row: dict, *, running: bool) -> dict:
    status = str(item_row.get("status", JobStatus.PENDING.value))
    if running and status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
        status = JobStatus.RUNNING.value
    return {
        "row_number": int(item_row.get("row_number", 0)),
        "job_id": item_row.get("job_id"),
        "job_name": item_row.get("job_name"),
        "source_username": item_row.get("source_username"),
        "destination_username": item_row.get("destination_username"),
        "status": status,
        "running": running,
        "migrated_count": int(item_row.get("migrated_count", 0)),
        "skipped_count": int(item_row.get("skipped_count", 0)),
        "failed_count": int(item_row.get("failed_count", 0)),
        "last_error": item_row.get("last_error"),
        "created_at": item_row.get("created_at"),
        "updated_at": item_row.get("updated_at"),
        "started_at": item_row.get("started_at"),
        "finished_at": item_row.get("finished_at"),
    }


def _batch_response(batch_row: dict, *, items: list[dict] | None = None) -> dict:
    response = {
        "batch_id": batch_row.get("batch_id"),
        "batch_name": batch_row.get("batch_name"),
        "status": batch_row.get("status"),
        "total_rows": int(batch_row.get("total_rows", 0)),
        "pending_rows": int(batch_row.get("pending_rows", 0)),
        "running_rows": int(batch_row.get("running_rows", 0)),
        "completed_rows": int(batch_row.get("completed_rows", 0)),
        "failed_rows": int(batch_row.get("failed_rows", 0)),
        "migrated_count": int(batch_row.get("migrated_count", 0)),
        "skipped_count": int(batch_row.get("skipped_count", 0)),
        "message_failed_count": int(batch_row.get("message_failed_count", 0)),
        "created_at": batch_row.get("created_at"),
        "updated_at": batch_row.get("updated_at"),
    }
    if items is not None:
        total_rows = max(response["total_rows"], len(items))
        pending_rows = running_rows = completed_rows = failed_rows = 0
        migrated_count = skipped_count = message_failed_count = 0
        for item in items:
            s = str(item.get("status", JobStatus.PENDING.value))
            if s == JobStatus.COMPLETED.value:
                completed_rows += 1
            elif s == JobStatus.FAILED.value:
                failed_rows += 1
            elif s == JobStatus.RUNNING.value:
                running_rows += 1
            else:
                pending_rows += 1
            migrated_count += int(item.get("migrated_count", 0))
            skipped_count += int(item.get("skipped_count", 0))
            message_failed_count += int(item.get("failed_count", 0))
        if total_rows > len(items):
            pending_rows += total_rows - len(items)
        response.update({
            "total_rows": total_rows,
            "pending_rows": pending_rows,
            "running_rows": running_rows,
            "completed_rows": completed_rows,
            "failed_rows": failed_rows,
            "migrated_count": migrated_count,
            "skipped_count": skipped_count,
            "message_failed_count": message_failed_count,
            "status": derive_batch_status(
                total_rows=total_rows,
                pending_rows=pending_rows,
                running_rows=running_rows,
                completed_rows=completed_rows,
                failed_rows=failed_rows,
            ),
            "items": items,
        })
    return response


def create_batches_router(
    state_store: SQLiteStateStore,
    background_jobs: BackgroundJobManager,
) -> APIRouter:
    router = APIRouter()

    @router.post("/batches/preflight")
    def preflight_batch(payload: BatchPreflightPayload) -> dict:
        try:
            rows = build_batch_rows(payload.csv_content, base_request_payload=payload.base_request)
            preview = build_batch_preview(rows)
            limit = payload.limit
            checked_rows = ok_rows = failed_rows = 0
            items: list[dict] = []
            for row in rows:
                item = row.to_preview_dict()
                if not row.valid or row.request is None:
                    item["preflight"] = None
                    item["preflight_skipped"] = True
                    failed_rows += 1
                    items.append(item)
                    continue
                if checked_rows >= limit:
                    item["preflight"] = None
                    item["preflight_skipped"] = True
                    items.append(item)
                    continue
                preflight_result = run_preflight(row.request, state_store=state_store)
                item["preflight"] = preflight_result
                item["preflight_skipped"] = False
                checked_rows += 1
                if preflight_result.get("overall_ok"):
                    ok_rows += 1
                else:
                    failed_rows += 1
                items.append(item)
            return {
                "total_rows": preview["total_rows"],
                "valid_rows": preview["valid_rows"],
                "invalid_rows": preview["invalid_rows"],
                "checked_rows": checked_rows,
                "ok_rows": ok_rows,
                "failed_rows": failed_rows,
                "items": items,
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/batches/preview")
    def preview_batch(payload: BatchPayload) -> dict:
        try:
            rows = build_batch_rows(payload.csv_content, base_request_payload=payload.base_request)
            return build_batch_preview(rows)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/batches/start")
    def start_batch(payload: BatchPayload) -> dict:
        try:
            rows = build_batch_rows(payload.csv_content, base_request_payload=payload.base_request)
            preview = build_batch_preview(rows)
            if preview["invalid_rows"] > 0 and not payload.allow_partial:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "CSV contains invalid rows. Run preview, fix rows, or set "
                            "allow_partial=true to start only valid entries."
                        ),
                        "preview": preview,
                    },
                )
            valid_rows = [row for row in rows if row.valid and row.request is not None]
            if not valid_rows:
                raise ValueError("No valid CSV rows to start.")
            batch_id = state_store.create_batch(
                batch_name=payload.batch_name, total_rows=preview["total_rows"]
            )
            for row in rows:
                if not row.valid or row.request is None:
                    state_store.add_batch_item(
                        batch_id=batch_id,
                        row_number=row.row_number,
                        source_username=row.source_username or "",
                        destination_username=row.destination_username or "",
                        submit_error=row.error or "Invalid CSV row.",
                        job_name=row.job_name,
                    )
                    continue
                job_id = state_store.create_job(row.request, MigrationPlan())
                submit_error = None
                try:
                    background_jobs.resume_job(request=row.request, job_id=job_id)
                except Exception as exc:
                    submit_error = str(exc)
                    state_store.set_job_status(
                        job_id, JobStatus.FAILED, set_finished=True, last_error=submit_error
                    )
                state_store.add_batch_item(
                    batch_id=batch_id,
                    row_number=row.row_number,
                    source_username=row.source_username,
                    destination_username=row.destination_username,
                    job_id=job_id,
                    job_name=row.job_name,
                    submit_error=submit_error,
                )
            batch_row = state_store.get_batch(batch_id)
            if not batch_row:
                raise RuntimeError("Unable to load created batch.")
            batch_items = state_store.list_batch_items(batch_id)
            response_items = [
                _batch_item_response(
                    item,
                    running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
                )
                for item in batch_items
            ]
            return _batch_response(batch_row, items=response_items)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/batches")
    def list_batches(limit: int = Query(default=20, ge=1, le=200)) -> dict:
        rows = state_store.list_batches(limit=limit)
        return {"items": [_batch_response(row) for row in rows]}

    @router.get("/batches/stream")
    async def batches_stream(
        request: Request,
        limit: int = Query(default=20, ge=1, le=200),
    ) -> StreamingResponse:
        async def event_generator():
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                rows = state_store.list_batches(limit=limit)
                yield _sse_message("batches", {"items": [_batch_response(row) for row in rows]})
                await asyncio.sleep(1.5)
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict:
        batch_row = state_store.get_batch(batch_id)
        if not batch_row:
            raise HTTPException(status_code=404, detail="Batch not found.")
        batch_items = state_store.list_batch_items(batch_id)
        response_items = [
            _batch_item_response(
                item,
                running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
            )
            for item in batch_items
        ]
        return _batch_response(batch_row, items=response_items)

    @router.get("/batches/{batch_id}/stream")
    async def batch_stream(batch_id: str, request: Request) -> StreamingResponse:
        async def event_generator():
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                batch_row = state_store.get_batch(batch_id)
                if not batch_row:
                    yield _sse_message("error", {"error": "Batch not found."})
                    break
                batch_items = state_store.list_batch_items(batch_id)
                response_items = [
                    _batch_item_response(
                        item,
                        running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
                    )
                    for item in batch_items
                ]
                yield _sse_message("batch", _batch_response(batch_row, items=response_items))
                await asyncio.sleep(1.0)
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return router
