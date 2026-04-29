from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from typing import Any

from groupware_migrator.engine.state import SQLiteStateStore


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(raw_value: str | None, fallback):
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return fallback


def _event_type_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type", "unknown"))
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def build_job_report(
    state_store: SQLiteStateStore,
    *,
    job_id: str,
    audit_event_limit: int = 2000,
) -> dict[str, Any]:
    job_row = state_store.get_job(job_id)
    if not job_row:
        raise ValueError(f"Job {job_id} does not exist.")

    request_payload = _safe_json_loads(job_row.get("request_json"), {})
    plan_payload = _safe_json_loads(job_row.get("plan_json"), {})
    events = state_store.list_audit_events(job_id, limit=audit_event_limit)

    migrated_count = int(job_row.get("migrated_count", 0))
    skipped_count = int(job_row.get("skipped_count", 0))
    failed_count = int(job_row.get("failed_count", 0))
    processed = migrated_count + skipped_count + failed_count

    return {
        "generated_at": _iso_now(),
        "job": {
            "job_id": job_row.get("job_id"),
            "job_name": job_row.get("job_name"),
            "status": job_row.get("status"),
            "source_protocol": job_row.get("source_protocol"),
            "destination_protocol": job_row.get("destination_protocol"),
            "dry_run": bool(job_row.get("dry_run")),
            "created_at": job_row.get("created_at"),
            "updated_at": job_row.get("updated_at"),
            "started_at": job_row.get("started_at"),
            "finished_at": job_row.get("finished_at"),
            "last_error": job_row.get("last_error"),
        },
        "metrics": {
            "migrated_count": migrated_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "processed_count": processed,
            "success_rate": (migrated_count / processed) if processed else 0.0,
        },
        "plan": plan_payload,
        "request": request_payload,
        "audit": {
            "event_count": len(events),
            "event_type_counts": _event_type_counts(events),
            "events": events,
        },
    }


def build_job_report_csv(report_payload: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    job = report_payload.get("job", {})
    metrics = report_payload.get("metrics", {})
    audit = report_payload.get("audit", {})
    plan = report_payload.get("plan", {})

    writer.writerow(["section", "key", "value"])
    for key in [
        "job_id",
        "job_name",
        "status",
        "source_protocol",
        "destination_protocol",
        "dry_run",
        "created_at",
        "started_at",
        "finished_at",
        "last_error",
    ]:
        writer.writerow(["job", key, job.get(key, "")])

    for key in [
        "migrated_count",
        "skipped_count",
        "failed_count",
        "processed_count",
        "success_rate",
    ]:
        writer.writerow(["metrics", key, metrics.get(key, "")])

    event_type_counts = audit.get("event_type_counts", {})
    if isinstance(event_type_counts, dict):
        for event_type, count in sorted(event_type_counts.items()):
            writer.writerow(["audit", f"event_type:{event_type}", count])

    for item in plan.get("items", []):
        source_mailbox = item.get("source_mailbox", "")
        destination_mailbox = item.get("destination_mailbox", "")
        estimated = item.get("estimated_messages", "")
        writer.writerow(
            [
                "plan",
                f"{source_mailbox} -> {destination_mailbox}",
                estimated,
            ]
        )

    return buffer.getvalue()
