"""Background scheduler thread: fires migration jobs from stored cron/interval schedules."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from groupware_migrator.engine.cron import cron_next, parse_interval_seconds
from groupware_migrator.models import MigrationRequest

if TYPE_CHECKING:
    from groupware_migrator.engine.background import BackgroundJobManager
    from groupware_migrator.engine.state import SQLiteStateStore

logger = logging.getLogger(__name__)

_TICK_INTERVAL = 30  # seconds


class SchedulerThread:
    def __init__(
        self,
        *,
        state_store: "SQLiteStateStore",
        job_manager: "BackgroundJobManager",
    ):
        self._state_store = state_store
        self._job_manager = job_manager
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()
        logger.info("Scheduler started (interval: %ds).", _TICK_INTERVAL)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(timeout=_TICK_INTERVAL):
            try:
                self._tick()
            except Exception as exc:
                logger.error("Scheduler tick error: %s", exc)

    def _tick(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        due = self._state_store.list_due_schedules(before=now_iso)
        for schedule in due:
            self._fire(schedule)

    def _fire(self, schedule: dict) -> None:
        schedule_id = schedule["id"]
        try:
            request_dict = json.loads(schedule["request_json"])
            request = MigrationRequest.from_dict(request_dict)
            user_id = schedule.get("user_id") or None
            job_id = self._job_manager.start_job(request, user_id=user_id)
            next_run = _next_run_after_now(schedule)
            self._state_store.update_schedule_after_fire(
                schedule_id=schedule_id,
                job_id=job_id,
                next_run_at=next_run,
            )
            logger.info("Schedule %s fired job %s; next run at %s.", schedule_id, job_id, next_run)
        except Exception as exc:
            logger.error("Failed to fire schedule %s: %s", schedule_id, exc, exc_info=True)


def _next_run_after_now(schedule: dict) -> str:
    now = datetime.now(timezone.utc)
    expr = schedule["schedule_expr"]
    if schedule.get("schedule_type") == "interval":
        seconds = parse_interval_seconds(expr)
        return (now + timedelta(seconds=seconds)).isoformat()
    return cron_next(expr, after=now).isoformat()


def compute_next_run(schedule_type: str, schedule_expr: str) -> str:
    """Compute the next fire time for a new or updated schedule."""
    now = datetime.now(timezone.utc)
    if schedule_type == "interval":
        seconds = parse_interval_seconds(schedule_expr)
        return (now + timedelta(seconds=seconds)).isoformat()
    return cron_next(schedule_expr, after=now).isoformat()
