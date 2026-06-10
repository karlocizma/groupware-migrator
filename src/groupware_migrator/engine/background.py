from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading

from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import JobStatus, MigrationPlan, MigrationRequest


class BackgroundJobManager:
    def __init__(
        self,
        *,
        state_store: SQLiteStateStore,
        runner: MigrationRunner,
        max_workers: int = 4,
    ):
        self._state_store = state_store
        self._runner = runner
        self._executor = ThreadPoolExecutor(
            max_workers=max(max_workers, 1),
            thread_name_prefix="migration-worker",
        )
        self._futures: dict[str, Future] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _forget_job(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)
            self._stop_events.pop(job_id, None)

    def _submit(self, *, job_id: str, request: MigrationRequest) -> None:
        with self._lock:
            existing = self._futures.get(job_id)
            if existing and not existing.done():
                raise ValueError(f"Job {job_id} is already running.")

            stop_event = threading.Event()
            self._stop_events[job_id] = stop_event
            future = self._executor.submit(
                self._runner.run,
                request=request,
                resume_job_id=job_id,
                stop_event=stop_event,
            )
            self._futures[job_id] = future
            future.add_done_callback(lambda _future: self._forget_job(job_id))

    def start_job(self, request: MigrationRequest, user_id: str | None = None) -> str:
        job_id = self._state_store.create_job(request=request, plan=MigrationPlan(), user_id=user_id)
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

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
