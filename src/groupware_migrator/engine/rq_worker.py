"""Worker process for the Redis-backed job queue.

Start with:
    groupware-migrator-worker [--redis-url redis://localhost:6379] \\
                               [--db-path data/state.db]

Or via environment variables:
    REDIS_URL=redis://localhost:6379
    DATABASE_URL=postgresql://user:pass@host/db (overrides --db-path)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

logger = logging.getLogger(__name__)

_SHUTDOWN = threading.Event()


def _handle_signal(signum: int, _frame: object) -> None:
    logger.info("Worker received signal %d; draining current job and stopping.", signum)
    _SHUTDOWN.set()


def _run_job(
    *,
    job_id: str,
    request_dict: dict,
    db_path: str | None,
    database_url: str | None,
    redis_client: object,
) -> None:
    from groupware_migrator.engine.postgres_state import create_state_store  # noqa: PLC0415
    from groupware_migrator.engine.runner import MigrationRunner  # noqa: PLC0415
    from groupware_migrator.models import MigrationRequest  # noqa: PLC0415
    from groupware_migrator.engine.redis_jobs import CANCEL_PREFIX, RUNNING_PREFIX, _RUNNING_TTL  # noqa: PLC0415

    state_store = create_state_store(state_db_path=db_path, database_url=database_url)
    runner = MigrationRunner(state_store=state_store)
    request = MigrationRequest.from_dict(request_dict)

    redis_client.setex(f"{RUNNING_PREFIX}{job_id}", _RUNNING_TTL, "1")
    stop_event = threading.Event()

    def _poll_cancel() -> None:
        while not stop_event.is_set():
            if redis_client.exists(f"{CANCEL_PREFIX}{job_id}"):
                logger.info("Cancellation flag set for job %s; stopping.", job_id)
                stop_event.set()
                break
            time.sleep(2)

    cancel_thread = threading.Thread(target=_poll_cancel, daemon=True)
    cancel_thread.start()

    try:
        runner.run(request=request, resume_job_id=job_id, stop_event=stop_event)
    except Exception as exc:
        logger.error("Job %s failed with exception: %s", job_id, exc)
    finally:
        stop_event.set()
        try:
            redis_client.delete(f"{RUNNING_PREFIX}{job_id}")
        except Exception:
            pass


def run_worker(*, redis_url: str, db_path: str | None, database_url: str | None) -> None:
    try:
        import redis  # noqa: PLC0415
    except ImportError:
        sys.exit(
            "redis package is required. Install: pip install 'groupware-migrator[redis]'"
        )

    from groupware_migrator.engine.redis_jobs import QUEUE_KEY  # noqa: PLC0415

    r = redis.from_url(redis_url, decode_responses=True)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Worker started. Redis: %s | Queue: %s | DB: %s",
        redis_url,
        QUEUE_KEY,
        database_url or db_path or "data/state.db",
    )

    while not _SHUTDOWN.is_set():
        result = r.blpop(QUEUE_KEY, timeout=5)
        if result is None:
            continue
        _, payload_bytes = result
        try:
            payload = json.loads(payload_bytes)
        except Exception as exc:
            logger.error("Failed to decode job payload: %s | raw: %s", exc, payload_bytes[:200])
            continue

        job_id = payload.get("job_id", "<unknown>")
        logger.info("Picked up job %s from queue.", job_id)
        try:
            _run_job(
                job_id=job_id,
                request_dict=payload.get("request", {}),
                db_path=payload.get("db_path") or db_path,
                database_url=payload.get("database_url") or database_url,
                redis_client=r,
            )
        except Exception as exc:
            logger.error("Unhandled error processing job %s: %s", job_id, exc)

    logger.info("Worker shut down cleanly.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Groupware Migrator Redis worker")
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        help="Redis connection URL (default: redis://localhost:6379 or $REDIS_URL)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (ignored when DATABASE_URL is set)",
    )
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL", "")
    run_worker(
        redis_url=args.redis_url,
        db_path=args.db_path,
        database_url=database_url or None,
    )


if __name__ == "__main__":
    main()
