"""Redis-backed job queue for horizontal scaling (Phase 12).

Drop-in replacement for BackgroundJobManager that enqueues jobs in Redis
instead of running them in local threads. Worker processes pop jobs and
execute them — start workers with:

    groupware-migrator-worker --redis-url redis://localhost:6379 \\
                               --db-path data/state.db

Install the dependency:

    pip install "groupware-migrator[redis]"
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING

from groupware_migrator.models import MigrationPlan, MigrationRequest

if TYPE_CHECKING:
    from groupware_migrator.engine.mailer import MailDeliveryManager
    from groupware_migrator.engine.runner import MigrationRunner
    from groupware_migrator.engine.state import SQLiteStateStore
    from groupware_migrator.engine.webhooks import WebhookDeliveryManager

logger = logging.getLogger(__name__)

QUEUE_KEY = "groupware_migrator:jobs"
CANCEL_PREFIX = "groupware_migrator:cancel:"
RUNNING_PREFIX = "groupware_migrator:running:"
_CANCEL_TTL = 3600
_RUNNING_TTL = 7200


class RedisJobManager:
    """Redis-backed job queue for horizontal scaling.

    Implements the same interface as ``BackgroundJobManager``. Jobs are
    created in the database and pushed onto a Redis LIST. Worker processes
    (``groupware-migrator-worker``) pop from the LIST and execute jobs.
    """

    def __init__(
        self,
        *,
        state_store: "SQLiteStateStore",
        redis_url: str = "redis://localhost:6379",
        webhook_manager: "WebhookDeliveryManager | None" = None,
        mail_manager: "MailDeliveryManager | None" = None,
        runner: "MigrationRunner | None" = None,
    ) -> None:
        try:
            import redis  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "redis is required for the Redis job queue. "
                "Install it with: pip install 'groupware-migrator[redis]'"
            ) from exc
        self._state_store = state_store
        self._webhook_manager = webhook_manager
        self._mail_manager = mail_manager
        self._runner = runner
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._lock = threading.Lock()
        self._accepting = True

    # ------------------------------------------------------------------
    # Public interface (mirrors BackgroundJobManager)
    # ------------------------------------------------------------------

    def start_job(
        self,
        request: MigrationRequest,
        user_id: str | None = None,
        priority: str = "normal",
    ) -> str:
        """Create a job record in the database and push it onto the Redis queue."""
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Job manager is shutting down.")
        job_id = self._state_store.create_job(
            request=request,
            plan=MigrationPlan(),
            user_id=user_id,
            priority=priority,
        )
        self._enqueue(job_id=job_id, request=request)
        logger.info("Enqueued job %s on Redis queue %s", job_id, QUEUE_KEY)
        return job_id

    def resume_job(self, *, request: MigrationRequest, job_id: str) -> str:
        """Re-enqueue an existing job (e.g. after manual intervention)."""
        if not self._state_store.has_job(job_id):
            raise ValueError(f"Job {job_id} does not exist.")
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Job manager is shutting down.")
        self._enqueue(job_id=job_id, request=request)
        logger.info("Re-enqueued job %s on Redis queue %s", job_id, QUEUE_KEY)
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        """Signal a running worker to stop this job.

        Sets a cancellation flag in Redis (workers poll it every 2 s) and
        also marks the job cancelled in the database for jobs not yet picked
        up by a worker.
        """
        self._redis.setex(f"{CANCEL_PREFIX}{job_id}", _CANCEL_TTL, "1")
        self._state_store.cancel_job(job_id)
        return True

    def is_running(self, job_id: str) -> bool:
        """True if a worker currently holds an active running key for this job."""
        return bool(self._redis.exists(f"{RUNNING_PREFIX}{job_id}"))

    def running_count(self) -> int:
        """Count jobs currently in *running* state according to the database."""
        stats = self._state_store.system_stats()
        return int(stats.get("jobs_running", 0))

    def drain(self, timeout: float) -> int:
        """Wait up to *timeout* seconds for all running jobs to finish.

        Returns the number of jobs still running after the timeout.
        """
        self._accepting = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.running_count() == 0:
                return 0
            time.sleep(1)
        return self.running_count()

    def shutdown(self, *, wait: bool = False) -> None:
        """Stop accepting new jobs. Workers continue until their jobs finish."""
        self._accepting = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enqueue(self, *, job_id: str, request: MigrationRequest) -> None:
        db_path: str | None = None
        database_url: str | None = None
        try:
            db_path = str(self._state_store.db_path)
        except NotImplementedError:
            database_url = getattr(self._state_store, "_database_url", None)
        payload = json.dumps(
            {
                "job_id": job_id,
                "request": request.to_dict(),
                "db_path": db_path,
                "database_url": database_url,
            }
        )
        self._redis.rpush(QUEUE_KEY, payload)
