from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from groupware_migrator.connectors.factory import create_source_connector
from groupware_migrator.engine.batch import build_batch_preview, build_batch_rows
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.preflight import run_preflight
from groupware_migrator.engine.reporting import build_job_report, build_job_report_csv
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import JobStatus, MigrationPlan, MigrationRequest
from groupware_migrator.providers import get_provider_presets


def _parse_json_blob(raw_payload: str) -> dict:
    if not raw_payload:
        return {}
    return json.loads(raw_payload)


def _job_response(job_row: dict, *, running: bool, include_payload: bool) -> dict:
    plan_payload = _parse_json_blob(job_row.get("plan_json", ""))
    request_payload = _parse_json_blob(job_row.get("request_json", ""))
    workload = str(request_payload.get("workload", "mail"))
    total_estimated_items = int(
        plan_payload.get(
            "total_estimated_items",
            plan_payload.get("total_estimated_messages", 0),
        )
    )
    collections = len(plan_payload.get("items", []))

    response = {
        "job_id": job_row["job_id"],
        "job_name": job_row["job_name"],
        "status": job_row["status"],
        "running": running,
        "workload": workload,
        "source_protocol": job_row["source_protocol"],
        "destination_protocol": job_row["destination_protocol"],
        "dry_run": bool(job_row["dry_run"]),
        "migrated_count": job_row["migrated_count"],
        "skipped_count": job_row["skipped_count"],
        "failed_count": job_row["failed_count"],
        "created_at": job_row["created_at"],
        "updated_at": job_row["updated_at"],
        "started_at": job_row["started_at"],
        "finished_at": job_row["finished_at"],
        "last_error": job_row["last_error"],
        "plan_summary": {
            "collections": collections,
            "total_estimated_items": total_estimated_items,
            "mailboxes": collections,
            "total_estimated_messages": total_estimated_items,
        },
    }
    if include_payload:
        response["request"] = request_payload
        response["plan"] = plan_payload
    return response


def _migration_request_from_payload(payload: dict) -> MigrationRequest:
    request_payload = payload.get("request", payload)
    return MigrationRequest.from_dict(request_payload)


def _sse_message(event: str, payload: dict) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"
def _batch_status_from_counts(
    *,
    total_rows: int,
    pending_rows: int,
    running_rows: int,
    completed_rows: int,
    failed_rows: int,
) -> str:
    if total_rows <= 0:
        return JobStatus.PENDING.value
    if completed_rows + failed_rows >= total_rows:
        return JobStatus.FAILED.value if failed_rows > 0 else JobStatus.COMPLETED.value
    if running_rows > 0:
        return JobStatus.RUNNING.value
    if pending_rows > 0:
        return JobStatus.PENDING.value
    return JobStatus.RUNNING.value


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
        pending_rows = 0
        running_rows = 0
        completed_rows = 0
        failed_rows = 0
        migrated_count = 0
        skipped_count = 0
        message_failed_count = 0
        for item in items:
            status = str(item.get("status", JobStatus.PENDING.value))
            if status == JobStatus.COMPLETED.value:
                completed_rows += 1
            elif status == JobStatus.FAILED.value:
                failed_rows += 1
            elif status == JobStatus.RUNNING.value:
                running_rows += 1
            else:
                pending_rows += 1
            migrated_count += int(item.get("migrated_count", 0))
            skipped_count += int(item.get("skipped_count", 0))
            message_failed_count += int(item.get("failed_count", 0))
        if total_rows > len(items):
            pending_rows += total_rows - len(items)

        response.update(
            {
                "total_rows": total_rows,
                "pending_rows": pending_rows,
                "running_rows": running_rows,
                "completed_rows": completed_rows,
                "failed_rows": failed_rows,
                "migrated_count": migrated_count,
                "skipped_count": skipped_count,
                "message_failed_count": message_failed_count,
                "status": _batch_status_from_counts(
                    total_rows=total_rows,
                    pending_rows=pending_rows,
                    running_rows=running_rows,
                    completed_rows=completed_rows,
                    failed_rows=failed_rows,
                ),
                "items": items,
            }
        )
    return response


def _batch_payload_from_request(payload: dict) -> tuple[str | None, bool, dict, str]:
    csv_content = payload.get("csv_content")
    if not isinstance(csv_content, str) or not csv_content.strip():
        raise ValueError("Missing required string field: csv_content.")

    base_request_payload = payload.get("base_request", payload.get("request"))
    if not isinstance(base_request_payload, dict):
        raise ValueError("Missing required object field: base_request.")

    batch_name = payload.get("batch_name")
    if batch_name is not None:
        batch_name = str(batch_name).strip() or None
    allow_partial = bool(payload.get("allow_partial", False))
    return batch_name, allow_partial, base_request_payload, csv_content


def create_app(*, state_db_path: str = "data/state.db") -> FastAPI:
    app = FastAPI(title="Groupware Migrator", version="0.3.0")
    state_store = SQLiteStateStore(Path(state_db_path))
    runner = MigrationRunner(state_store=state_store)
    background_jobs = BackgroundJobManager(
        state_store=state_store,
        runner=runner,
    )

    app.state.state_store = state_store
    app.state.runner = runner
    app.state.background_jobs = background_jobs

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

    @app.on_event("shutdown")
    def shutdown_background_workers() -> None:
        background_jobs.shutdown(wait=False)

    @app.get("/")
    def ui_index() -> FileResponse:
        index_file = static_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="UI assets not found.")
        return FileResponse(index_file)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/providers")
    @app.get("/api/providers")
    def list_providers() -> dict:
        return {"items": get_provider_presets()}

    @app.post("/jobs/preflight")
    @app.post("/api/jobs/preflight")
    def preflight_job(payload: dict) -> dict:
        try:
            request = _migration_request_from_payload(payload)
            return run_preflight(request, state_store=state_store)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/batches/preflight")
    @app.post("/api/batches/preflight")
    def preflight_batch(payload: dict) -> dict:
        try:
            _, _, base_request_payload, csv_content = _batch_payload_from_request(payload)
            limit = max(min(int(payload.get("limit", 20)), 200), 1)
            rows = build_batch_rows(
                csv_content,
                base_request_payload=base_request_payload,
            )
            preview = build_batch_preview(rows)

            checked_rows = 0
            ok_rows = 0
            failed_rows = 0
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
                preflight_result = run_preflight(
                    row.request,
                    state_store=state_store,
                )
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
    @app.post("/batches/preview")
    @app.post("/api/batches/preview")
    def preview_batch(payload: dict) -> dict:
        try:
            _, _, base_request_payload, csv_content = _batch_payload_from_request(payload)
            rows = build_batch_rows(
                csv_content,
                base_request_payload=base_request_payload,
            )
            return build_batch_preview(rows)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/batches/start")
    @app.post("/api/batches/start")
    def start_batch(payload: dict) -> dict:
        try:
            batch_name, allow_partial, base_request_payload, csv_content = (
                _batch_payload_from_request(payload)
            )
            rows = build_batch_rows(
                csv_content,
                base_request_payload=base_request_payload,
            )
            preview = build_batch_preview(rows)

            if preview["invalid_rows"] > 0 and not allow_partial:
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
                batch_name=batch_name,
                total_rows=preview["total_rows"],
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
                        job_id,
                        JobStatus.FAILED,
                        set_finished=True,
                        last_error=submit_error,
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
                    running=bool(item.get("job_id"))
                    and background_jobs.is_running(str(item["job_id"])),
                )
                for item in batch_items
            ]
            return _batch_response(batch_row, items=response_items)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/batches")
    @app.get("/api/batches")
    def list_batches(limit: int = Query(default=20, ge=1, le=200)) -> dict:
        rows = state_store.list_batches(limit=limit)
        return {"items": [_batch_response(row) for row in rows]}

    @app.get("/batches/stream")
    @app.get("/api/batches/stream")
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
                payload = {"items": [_batch_response(row) for row in rows]}
                yield _sse_message("batches", payload)
                await asyncio.sleep(1.5)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/batches/{batch_id}")
    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict:
        batch_row = state_store.get_batch(batch_id)
        if not batch_row:
            raise HTTPException(status_code=404, detail="Batch not found.")
        batch_items = state_store.list_batch_items(batch_id)
        response_items = [
            _batch_item_response(
                item,
                running=bool(item.get("job_id"))
                and background_jobs.is_running(str(item["job_id"])),
            )
            for item in batch_items
        ]
        return _batch_response(batch_row, items=response_items)

    @app.get("/batches/{batch_id}/stream")
    @app.get("/api/batches/{batch_id}/stream")
    async def batch_stream(
        batch_id: str,
        request: Request,
    ) -> StreamingResponse:
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
                        running=bool(item.get("job_id"))
                        and background_jobs.is_running(str(item["job_id"])),
                    )
                    for item in batch_items
                ]
                yield _sse_message(
                    "batch",
                    _batch_response(batch_row, items=response_items),
                )
                await asyncio.sleep(1.0)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post("/jobs/plan")
    @app.post("/api/jobs/plan")
    def plan_job(payload: dict) -> dict:
        try:
            request = _migration_request_from_payload(payload)
            source_connector = create_source_connector(request)
            plan = runner.plan(
                request=request,
                source_connector=source_connector,
            )
            return plan.to_dict()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/run")
    @app.post("/api/jobs/run")
    def run_job(payload: dict) -> dict:
        try:
            resume_job_id = payload.get("resume_job_id")
            request = _migration_request_from_payload(payload)
            report = runner.run(
                request=request,
                resume_job_id=resume_job_id,
            )
            return report.to_dict()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/start")
    @app.post("/api/jobs/start")
    def start_background_job(payload: dict) -> dict:
        try:
            request = _migration_request_from_payload(payload)
            job_id = background_jobs.start_job(request=request)
            job_row = state_store.get_job(job_id)
            if not job_row:
                raise RuntimeError("Unable to read background job after creation.")
            return _job_response(job_row, running=True, include_payload=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/resume")
    @app.post("/api/jobs/resume")
    def resume_background_job(payload: dict) -> dict:
        try:
            if "job_id" not in payload:
                raise ValueError("Missing required field: job_id.")
            job_id = str(payload["job_id"])
            request = _migration_request_from_payload(payload)
            background_jobs.resume_job(request=request, job_id=job_id)
            job_row = state_store.get_job(job_id)
            if not job_row:
                raise RuntimeError(f"Unable to read resumed job {job_id}.")
            return _job_response(job_row, running=True, include_payload=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/jobs")
    @app.get("/api/jobs")
    def list_jobs(limit: int = Query(default=20, ge=1, le=200)) -> dict:
        rows = state_store.list_jobs(limit=limit)
        items = [
            _job_response(
                row,
                running=background_jobs.is_running(str(row["job_id"])),
                include_payload=False,
            )
            for row in rows
        ]
        return {"items": items}

    @app.get("/jobs/{job_id}")
    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job_row = state_store.get_job(job_id)
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _job_response(
            job_row,
            running=background_jobs.is_running(job_id),
            include_payload=True,
        )

    @app.get("/jobs/{job_id}/events")
    @app.get("/api/jobs/{job_id}/events")
    def get_job_events(
        job_id: str,
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict:
        if not state_store.has_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"items": state_store.list_audit_events(job_id, limit=limit)}

    @app.get("/jobs/{job_id}/report")
    @app.get("/api/jobs/{job_id}/report")
    def get_job_report(
        job_id: str,
        format: str = Query(default="json"),
        audit_limit: int = Query(default=2000, ge=1, le=5000),
    ):
        if not state_store.has_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        report_payload = build_job_report(
            state_store,
            job_id=job_id,
            audit_event_limit=audit_limit,
        )
        if format.lower() == "csv":
            csv_payload = build_job_report_csv(report_payload)
            return PlainTextResponse(
                csv_payload,
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=job-{job_id}-report.csv"
                },
            )
        return report_payload

    @app.get("/jobs/stream")
    @app.get("/api/jobs/stream")
    async def jobs_stream(
        request: Request,
        limit: int = Query(default=30, ge=1, le=200),
    ) -> StreamingResponse:
        async def event_generator():
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                rows = state_store.list_jobs(limit=limit)
                payload = {
                    "items": [
                        _job_response(
                            row,
                            running=background_jobs.is_running(str(row["job_id"])),
                            include_payload=False,
                        )
                        for row in rows
                    ]
                }
                yield _sse_message("jobs", payload)
                await asyncio.sleep(1.5)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/jobs/{job_id}/stream")
    @app.get("/api/jobs/{job_id}/stream")
    async def job_stream(
        job_id: str,
        request: Request,
    ) -> StreamingResponse:
        async def event_generator():
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                row = state_store.get_job(job_id)
                if not row:
                    payload = {"error": "Job not found."}
                    yield _sse_message("error", payload)
                    break
                payload = _job_response(
                    row,
                    running=background_jobs.is_running(job_id),
                    include_payload=True,
                )
                yield _sse_message("job", payload)
                await asyncio.sleep(1.0)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app