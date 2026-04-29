from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.connectors.factory import (
    create_destination_connector,
    create_source_connector,
)
from groupware_migrator.engine.planner import MigrationPlanner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import MigrationRequest, SyncMode, WorkloadType


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exc_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def run_preflight(
    request: MigrationRequest,
    *,
    source_connector: SourceConnector | None = None,
    destination_connector: DestinationConnector | None = None,
    state_store: SQLiteStateStore | None = None,
) -> dict[str, Any]:
    source_connector = source_connector or create_source_connector(request)
    destination_connector = destination_connector or create_destination_connector(request)
    planner = MigrationPlanner()

    result: dict[str, Any] = {
        "checked_at": _utcnow_iso(),
        "overall_ok": False,
        "workload": request.workload.value,
        "source": {"ok": False, "error": None},
        "destination": {"ok": False, "error": None},
        "plan": {
            "ok": False,
            "collections": 0,
            "total_estimated_items": 0,
            "mailboxes": 0,
            "total_estimated_messages": 0,
            "error": "Skipped because source validation has not completed.",
        },
        "warnings": [],
        "incremental": {
            "mode": request.options.sync_mode.value,
            "base_job_id": request.options.incremental_base_job_id,
            "resolved_cursor_collections": 0,
            "resolved_cursor_mailboxes": 0,
            "resolution_source": "disabled",
            "sync_key": None,
            "error": None,
        },
    }

    incremental_cursors: dict[str, str] = {}
    incremental_resolution_error: str | None = None
    if request.options.sync_mode is SyncMode.INCREMENTAL:
        result["incremental"]["resolution_source"] = "none"
        if state_store is None:
            incremental_resolution_error = (
                "Incremental mode requires state-backed cursor resolution."
            )
        else:
            try:
                incremental_cursors = state_store.resolve_incremental_cursors(
                    request,
                    base_job_id=request.options.incremental_base_job_id,
                )
                result["incremental"]["resolved_cursor_collections"] = len(
                    incremental_cursors
                )
                result["incremental"]["resolved_cursor_mailboxes"] = len(
                    incremental_cursors
                )
                result["incremental"]["resolution_source"] = (
                    "base_job"
                    if request.options.incremental_base_job_id
                    else "sync_state"
                    if incremental_cursors
                    else "none"
                )
                result["incremental"]["sync_key"] = state_store.build_sync_key(
                    request
                )
                if not incremental_cursors:
                    result["warnings"].append(
                        "Incremental mode has no existing cursors; first run may estimate from full collection counts."
                    )
            except Exception as exc:
                incremental_resolution_error = _exc_message(exc)
        if incremental_resolution_error:
            result["incremental"]["error"] = incremental_resolution_error

    try:
        source_connector.validate()
        result["source"]["ok"] = True
    except Exception as exc:
        result["source"]["error"] = _exc_message(exc)
        result["plan"]["error"] = "Skipped because source connection validation failed."

    try:
        destination_connector.validate()
        result["destination"]["ok"] = True
    except Exception as exc:
        result["destination"]["error"] = _exc_message(exc)

    if result["source"]["ok"] and incremental_resolution_error is None:
        try:
            plan = planner.build_plan(
                request,
                source_connector,
                incremental_cursors=incremental_cursors,
            )
            result["plan"] = {
                "ok": True,
                "collections": len(plan.items),
                "total_estimated_items": int(plan.total_estimated_items),
                "mailboxes": len(plan.items),
                "total_estimated_messages": int(plan.total_estimated_messages),
                "error": None,
            }
            if not plan.items:
                if request.workload is WorkloadType.MAIL:
                    result["warnings"].append("Source returned zero mailboxes.")
                else:
                    result["warnings"].append("Source returned zero collections.")
        except Exception as exc:
            result["plan"] = {
                "ok": False,
                "collections": 0,
                "total_estimated_items": 0,
                "mailboxes": 0,
                "total_estimated_messages": 0,
                "error": _exc_message(exc),
            }
    elif incremental_resolution_error is not None:
        result["plan"]["error"] = (
            "Skipped because incremental cursor resolution failed."
        )

    result["overall_ok"] = bool(
        result["source"]["ok"] and result["destination"]["ok"] and result["plan"]["ok"]
    )
    return result
