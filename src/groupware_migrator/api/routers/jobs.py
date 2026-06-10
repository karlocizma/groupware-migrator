from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from groupware_migrator.connectors.factory import create_source_connector
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.preflight import run_preflight
from groupware_migrator.engine.reporting import build_job_report, build_job_report_csv
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import MigrationPlan, MigrationRequest


def _parse_json_blob(raw_payload: str) -> dict:
    if not raw_payload:
        return {}
    return json.loads(raw_payload)


def _migration_request_from_payload(payload: dict) -> MigrationRequest:
    request_payload = payload.get("request", payload)
    return MigrationRequest.from_dict(request_payload)


def _sse_message(event: str, payload: dict) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


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


def create_jobs_router(
    state_store: SQLiteStateStore,
    background_jobs: BackgroundJobManager,
    runner: MigrationRunner,
) -> APIRouter:
    router = APIRouter()

    @router.post("/jobs/preflight")
    def preflight_job(payload: dict) -> dict:
        try:
            request = _migration_request_from_payload(payload)
            return run_preflight(request, state_store=state_store)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/jobs/plan")
    def plan_job(payload: dict) -> dict:
        try:
            request = _migration_request_from_payload(payload)
            source_connector = create_source_connector(request)
            plan = runner.plan(request=request, source_connector=source_connector)
            return plan.to_dict()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/jobs/run")
    def run_job(payload: dict) -> dict:
        try:
            resume_job_id = payload.get("resume_job_id")
            request = _migration_request_from_payload(payload)
            report = runner.run(request=request, resume_job_id=resume_job_id)
            return report.to_dict()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/jobs/start")
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

    @router.post("/jobs/resume")
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

    @router.get("/jobs")
    def list_jobs(limit: int = Query(default=20, ge=1, le=200)) -> dict:
        rows = state_store.list_jobs(limit=limit)
        return {
            "items": [
                _job_response(
                    row,
                    running=background_jobs.is_running(str(row["job_id"])),
                    include_payload=False,
                )
                for row in rows
            ]
        }

    @router.get("/jobs/stream")
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

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job_row = state_store.get_job(job_id)
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _job_response(job_row, running=background_jobs.is_running(job_id), include_payload=True)

    @router.get("/jobs/{job_id}/events")
    def get_job_events(
        job_id: str,
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict:
        if not state_store.has_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"items": state_store.list_audit_events(job_id, limit=limit)}

    @router.get("/jobs/{job_id}/report")
    def get_job_report(
        job_id: str,
        format: str = Query(default="json"),
        audit_limit: int = Query(default=2000, ge=1, le=5000),
    ):
        if not state_store.has_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        report_payload = build_job_report(
            state_store, job_id=job_id, audit_event_limit=audit_limit
        )
        if format.lower() == "csv":
            csv_payload = build_job_report_csv(report_payload)
            return PlainTextResponse(
                csv_payload,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=job-{job_id}-report.csv"},
            )
        return report_payload

    @router.get("/jobs/{job_id}/stream")
    async def job_stream(job_id: str, request: Request) -> StreamingResponse:
        async def event_generator():
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                row = state_store.get_job(job_id)
                if not row:
                    yield _sse_message("error", {"error": "Job not found."})
                    break
                yield _sse_message(
                    "job",
                    _job_response(row, running=background_jobs.is_running(job_id), include_payload=True),
                )
                await asyncio.sleep(1.0)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return router
