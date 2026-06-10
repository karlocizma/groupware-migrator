from __future__ import annotations

import logging
import threading
from datetime import datetime

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.connectors.factory import (
    create_destination_connector,
    create_source_connector,
)
from groupware_migrator.engine.idempotency import (
    build_item_fingerprint,
    build_message_fingerprint,
)
from groupware_migrator.engine.planner import MigrationPlanner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import (
    JobStatus,
    MigrationReport,
    MigrationRequest,
    SyncMode,
    WorkloadType,
)

logger = logging.getLogger(__name__)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class MigrationRunner:
    def __init__(self, state_store: SQLiteStateStore):
        self._state_store = state_store
        self._planner = MigrationPlanner()

    def _audit(
        self,
        job_id: str,
        event_type: str,
        *,
        event_level: str = "info",
        payload: dict | None = None,
    ) -> None:
        try:
            self._state_store.append_audit_event(
                job_id,
                event_type,
                event_level=event_level,
                payload=payload or {},
            )
        except Exception as exc:
            # Auditing should not break migration execution.
            logger.warning("Failed to write audit event %s for job %s: %s", event_type, job_id, exc)
            return

    def _resolve_incremental_cursors(self, request: MigrationRequest) -> dict[str, str]:
        if request.options.sync_mode is not SyncMode.INCREMENTAL:
            return {}
        return self._state_store.resolve_incremental_cursors(
            request,
            base_job_id=request.options.incremental_base_job_id,
        )

    def plan(
        self,
        request: MigrationRequest,
        *,
        source_connector: SourceConnector | None = None,
    ):
        source_connector = source_connector or create_source_connector(request)
        source_connector.validate()
        incremental_cursors = self._resolve_incremental_cursors(request)
        return self._planner.build_plan(
            request,
            source_connector,
            incremental_cursors=incremental_cursors,
        )

    def run(
        self,
        request: MigrationRequest,
        *,
        source_connector: SourceConnector | None = None,
        destination_connector: DestinationConnector | None = None,
        resume_job_id: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> MigrationReport:
        source_connector = source_connector or create_source_connector(request)
        destination_connector = destination_connector or create_destination_connector(request)
        incremental_cursors = self._resolve_incremental_cursors(request)
        plan = self._planner.build_plan(
            request,
            source_connector,
            incremental_cursors=incremental_cursors,
        )

        if resume_job_id:
            job_id = resume_job_id
            existing_job = self._state_store.get_job(job_id)
            if not existing_job:
                raise ValueError(f"Job {job_id} does not exist.")
            if existing_job["status"] == JobStatus.COMPLETED.value:
                return self._build_report(existing_job, [])
            mark_started = existing_job["started_at"] is None
        else:
            job_id = self._state_store.create_job(request, plan)
            mark_started = True
        self._state_store.update_job_plan(job_id, plan)
        logger.info("Job %s starting (workload=%s, dry_run=%s)", job_id, request.workload.value, request.options.dry_run)

        self._state_store.set_job_status(
            job_id,
            JobStatus.RUNNING,
            set_started=mark_started,
        )
        self._audit(
            job_id,
            "job_started",
            payload={
                "source_protocol": request.source.protocol.value,
                "destination_protocol": request.destination.protocol.value,
                "dry_run": request.options.dry_run,
                "resume": bool(resume_job_id),
                "collections": len(plan.items),
                "mailboxes": len(plan.items),
            },
        )

        error_messages: list[str] = []
        run_failures = 0
        max_errors = max(request.options.max_errors, 1)

        try:
            source_connector.validate()
            destination_connector.validate()
            for collection_plan in plan.items:
                if stop_event and stop_event.is_set():
                    self._state_store.set_job_status(job_id, JobStatus.CANCELLED, set_finished=True)
                    self._audit(job_id, "job_cancelled", payload={"reason": "Cancelled by user."})
                    job_row = self._state_store.get_job(job_id)
                    if not job_row:
                        raise RuntimeError(f"Unable to load job state for {job_id}.")
                    return self._build_report(job_row, error_messages)
                collection_migrated = 0
                collection_skipped = 0
                collection_failed = 0
                checkpoint = self._state_store.get_checkpoint(
                    job_id,
                    collection_plan.source_collection,
                )
                if checkpoint is None:
                    checkpoint = incremental_cursors.get(collection_plan.source_collection)
                    if checkpoint is not None:
                        self._state_store.set_checkpoint(
                            job_id,
                            collection_plan.source_collection,
                            checkpoint,
                        )
                event_prefix = (
                    "mailbox" if request.workload is WorkloadType.MAIL else "collection"
                )
                self._audit(
                    job_id,
                    f"{event_prefix}_started",
                    payload={
                        "source_collection": collection_plan.source_collection,
                        "destination_collection": collection_plan.destination_collection,
                        "source_mailbox": collection_plan.source_collection,
                        "destination_mailbox": collection_plan.destination_collection,
                        "resume_from": checkpoint,
                    },
                )
                if not request.options.dry_run:
                    destination_connector.ensure_collection(
                        collection_plan.destination_collection
                    )

                for item in source_connector.iter_items(
                    collection_plan.source_collection,
                    resume_from=checkpoint,
                ):
                    item_metadata = dict(item.metadata)
                    if request.workload is WorkloadType.MAIL:
                        fingerprint = build_message_fingerprint(
                            item.raw_payload,
                            source_id=item.source_id,
                            message_id=item_metadata.get("message_id") or item.item_key,
                        )
                    else:
                        fingerprint = build_item_fingerprint(
                            source_collection=item.source_collection,
                            source_id=item.source_id,
                            raw_payload=item.raw_payload,
                            version_token=item.version_token,
                        )
                    if self._state_store.has_fingerprint(job_id, fingerprint):
                        self._state_store.increment_counters(job_id, skipped=1)
                        collection_skipped += 1
                        self._state_store.set_checkpoint(
                            job_id,
                            collection_plan.source_collection,
                            item.source_id,
                        )
                        self._audit(
                            job_id,
                            "item_skipped_duplicate",
                            payload={
                                "source_collection": collection_plan.source_collection,
                                "source_mailbox": collection_plan.source_collection,
                                "source_id": item.source_id,
                            },
                        )
                        continue

                    try:
                        destination_id = None
                        if not request.options.dry_run:
                            destination_id = destination_connector.upsert_item(
                                collection_plan.destination_collection,
                                item.source_id,
                                item.raw_payload,
                                metadata=item_metadata,
                            )
                            inserted = self._state_store.record_message(
                                job_id,
                                fingerprint=fingerprint,
                                source_mailbox=collection_plan.source_collection,
                                source_id=item.source_id,
                                destination_mailbox=collection_plan.destination_collection,
                                destination_id=destination_id,
                            )
                            if inserted:
                                self._state_store.increment_counters(job_id, migrated=1)
                                collection_migrated += 1
                            else:
                                self._state_store.increment_counters(job_id, skipped=1)
                                collection_skipped += 1
                        else:
                            self._state_store.increment_counters(job_id, migrated=1)
                            collection_migrated += 1

                        self._state_store.set_checkpoint(
                            job_id,
                            collection_plan.source_collection,
                            item.source_id,
                        )
                        self._audit(
                            job_id,
                            "item_migrated",
                            payload={
                                "source_collection": collection_plan.source_collection,
                                "destination_collection": collection_plan.destination_collection,
                                "source_mailbox": collection_plan.source_collection,
                                "destination_mailbox": collection_plan.destination_collection,
                                "source_id": item.source_id,
                                "destination_id": destination_id,
                                "dry_run": request.options.dry_run,
                                "content_type": item.content_type,
                            },
                        )
                    except Exception as exc:
                        run_failures += 1
                        collection_failed += 1
                        self._state_store.increment_counters(job_id, failed=1)
                        error_message = (
                            f"{collection_plan.source_collection}:{item.source_id}:{exc}"
                        )
                        error_messages.append(error_message)
                        self._audit(
                            job_id,
                            "item_failed",
                            event_level="error",
                            payload={
                                "source_collection": collection_plan.source_collection,
                                "destination_collection": collection_plan.destination_collection,
                                "source_mailbox": collection_plan.source_collection,
                                "destination_mailbox": collection_plan.destination_collection,
                                "source_id": item.source_id,
                                "error": str(exc),
                            },
                        )
                        if run_failures >= max_errors:
                            raise RuntimeError(
                                f"Maximum error threshold reached ({max_errors})."
                            ) from exc
                self._audit(
                    job_id,
                    f"{event_prefix}_completed",
                    payload={
                        "source_collection": collection_plan.source_collection,
                        "destination_collection": collection_plan.destination_collection,
                        "source_mailbox": collection_plan.source_collection,
                        "destination_mailbox": collection_plan.destination_collection,
                        "migrated": collection_migrated,
                        "skipped": collection_skipped,
                        "failed": collection_failed,
                    },
                )

            self._state_store.set_job_status(
                job_id,
                JobStatus.COMPLETED,
                set_finished=True,
                last_error=error_messages[-1] if error_messages else None,
            )
            if (
                request.options.sync_mode is SyncMode.INCREMENTAL
                and not request.options.dry_run
            ):
                self._state_store.update_sync_cursors_from_job(request, job_id=job_id)
                updated_cursor_count = len(self._state_store.get_job_checkpoints(job_id))
                self._audit(
                    job_id,
                    "sync_cursors_updated",
                    payload={
                        "cursor_collections": updated_cursor_count,
                        "cursor_mailboxes": updated_cursor_count,
                        "sync_mode": request.options.sync_mode.value,
                    },
                )
            job_row = self._state_store.get_job(job_id)
            if job_row:
                logger.info(
                    "Job %s completed (migrated=%d, skipped=%d, failed=%d)",
                    job_id,
                    int(job_row.get("migrated_count", 0)),
                    int(job_row.get("skipped_count", 0)),
                    int(job_row.get("failed_count", 0)),
                )
                self._audit(
                    job_id,
                    "job_completed",
                    payload={
                        "migrated_count": int(job_row["migrated_count"]),
                        "skipped_count": int(job_row["skipped_count"]),
                        "failed_count": int(job_row["failed_count"]),
                    },
                )
        except Exception as exc:
            if not error_messages:
                error_messages.append(str(exc))
            logger.error("Job %s failed: %s", job_id, error_messages[-1] if error_messages else str(exc))
            self._state_store.set_job_status(
                job_id,
                JobStatus.FAILED,
                set_finished=True,
                last_error=error_messages[-1],
            )
            self._audit(
                job_id,
                "job_failed",
                event_level="error",
                payload={
                    "error": error_messages[-1],
                    "max_errors": max_errors,
                },
            )

        job_row = self._state_store.get_job(job_id)
        if not job_row:
            raise RuntimeError(f"Unable to load job state for {job_id}.")
        return self._build_report(job_row, error_messages)

    def _build_report(self, job_row: dict, error_messages: list[str]) -> MigrationReport:
        return MigrationReport(
            job_id=str(job_row["job_id"]),
            status=JobStatus(str(job_row["status"])),
            migrated_count=int(job_row["migrated_count"]),
            skipped_count=int(job_row["skipped_count"]),
            failed_count=int(job_row["failed_count"]),
            dry_run=bool(job_row["dry_run"]),
            started_at=_parse_timestamp(job_row["started_at"]),
            finished_at=_parse_timestamp(job_row["finished_at"]),
            error_messages=error_messages,
        )
