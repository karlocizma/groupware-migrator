from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import threading
import time
from typing import TYPE_CHECKING

from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import JobStatus, MigrationPlan, MigrationRequest

if TYPE_CHECKING:
    from groupware_migrator.engine.webhooks import WebhookDeliveryManager

logger = logging.getLogger(__name__)

# Retry backoff: attempt 0→30s, 1→60s, 2→120s, 3→240s, capped at 300s
_RETRY_BASE_DELAY = 30
_RETRY_MAX_DELAY = 300


class BackgroundJobManager:
    def __init__(
        self,
        *,
        state_store: SQLiteStateStore,
        runner: MigrationRunner,
        max_workers: int = 4,
        webhook_manager: "WebhookDeliveryManager | None" = None,
    ):
        self._state_store = state_store
        self._runner = runner
        self._webhook_manager = webhook_manager
        self._executor = ThreadPoolExecutor(
            max_workers=max(max_workers, 1),
            thread_name_prefix="migration-worker",
        )
        self._futures: dict[str, Future] = {}
        self._stop_events: dict[str, threading.Event] = {}
        # job_id -> (request, retry_attempt) for retry tracking
        self._job_contexts: dict[str, tuple[MigrationRequest, int]] = {}
        self._lock = threading.Lock()
        self._accepting = True

    def _submit(self, *, job_id: str, request: MigrationRequest, retry_attempt: int = 0) -> None:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Job manager is shutting down.")
            existing = self._futures.get(job_id)
            if existing and not existing.done():
                raise ValueError(f"Job {job_id} is already running.")
            stop_event = threading.Event()
            self._stop_events[job_id] = stop_event
            self._job_contexts[job_id] = (request, retry_attempt)
            future = self._executor.submit(
                self._runner.run,
                request=request,
                resume_job_id=job_id,
                stop_event=stop_event,
            )
            self._futures[job_id] = future
            future.add_done_callback(lambda f: self._on_done(job_id, f))

    def _on_done(self, job_id: str, future: Future) -> None:
        with self._lock:
            self._futures.pop(job_id, None)
            self._stop_events.pop(job_id, None)
            context = self._job_contexts.pop(job_id, None)

        if context is None or future.exception() is not None:
            return

        request, retry_attempt = context

        # Fire webhook notifications for job completion/failure
        job_row = self._state_store.get_job(job_id)
        if job_row and self._webhook_manager is not None:
            status = job_row.get("status", "")
            event_type = "job.completed" if status == JobStatus.COMPLETED.value else "job.failed"
            user_id = job_row.get("user_id") or None
            payload = {
                "job_id": job_id,
                "job_name": job_row.get("job_name"),
                "status": status,
                "migrated_count": job_row.get("migrated_count", 0),
                "skipped_count": job_row.get("skipped_count", 0),
                "failed_count": job_row.get("failed_count", 0),
                "last_error": job_row.get("last_error"),
                "finished_at": job_row.get("finished_at"),
            }
            try:
                self._webhook_manager.fire(event_type=event_type, payload=payload, user_id=user_id)
            except Exception as exc:
                logger.error("Failed to fire webhooks for job %s: %s", job_id, exc)

        max_retries = getattr(request.options, "max_retries", 0)
        if retry_attempt >= max_retries:
            return

        if not job_row or job_row.get("status") != JobStatus.FAILED.value:
            return

        delay = min(_RETRY_BASE_DELAY * (2 ** retry_attempt), _RETRY_MAX_DELAY)
        logger.info(
            "Job %s failed (attempt %d/%d). Retrying in %ds.",
            job_id, retry_attempt + 1, max_retries, int(delay),
        )
        timer = threading.Timer(delay, self._schedule_retry, args=[job_id, request, retry_attempt + 1])
        timer.daemon = True
        timer.start()

    def _schedule_retry(self, job_id: str, request: MigrationRequest, retry_attempt: int) -> None:
        if not self._accepting:
            return
        try:
            self._state_store.increment_retry_count(job_id, retry_attempt)
            self._submit(job_id=job_id, request=request, retry_attempt=retry_attempt)
        except Exception as exc:
            logger.error("Failed to schedule retry for job %s: %s", job_id, exc)

    def start_job(
        self,
        request: MigrationRequest,
        user_id: str | None = None,
        priority: str = "normal",
    ) -> str:
        job_id = self._state_store.create_job(
            request=request, plan=MigrationPlan(), user_id=user_id, priority=priority
        )
        self._submit(job_id=job_id, request=request)
        return job_id

    def resume_job(self, *, request: MigrationRequest, job_id: str) -> str:
        if not self._state_store.has_job(job_id):
            raise ValueError(f"Job {job_id} does not exist.")
        self._submit(job_id=job_id, request=request)
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        """Signal a running job to stop or mark a pending job cancelled. Returns True if actioned."""
        with self._lock:
            event = self._stop_events.get(job_id)
            if event:
                event.set()
                return True
        return self._state_store.cancel_job(job_id)

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            if not future:
                return False
            return not future.done()

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures.values() if not f.done())

    def drain(self, timeout: float) -> int:
        """Stop accepting new jobs and wait up to timeout seconds for running jobs to finish.

        Returns the count of jobs still running after the timeout.
        """
        self._accepting = False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not any(not f.done() for f in self._futures.values()):
                    break
            time.sleep(0.5)
        return self.running_count()

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
