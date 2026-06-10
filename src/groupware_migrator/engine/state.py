from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import uuid
from typing import Any

import bcrypt as _bcrypt

from groupware_migrator.models import JobStatus, MigrationPlan, MigrationRequest


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    return _bcrypt.hashpw(pw_bytes, _bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
def derive_batch_status(
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


def _request_sync_identity(request: MigrationRequest) -> dict[str, Any]:
    source_include_collections = request.source.include_collections or []
    return {
        "workload": request.workload.value,
        "source_protocol": request.source.protocol.value,
        "source_host": request.source.connection.host,
        "source_port": int(request.source.connection.port),
        "source_username": request.source.connection.username,
        "source_provider_id": request.source.provider_id,
        "source_include_collections": sorted(source_include_collections),
        "source_include_mailboxes": sorted(source_include_collections),
        "destination_protocol": request.destination.protocol.value,
        "destination_host": request.destination.connection.host,
        "destination_port": int(request.destination.connection.port),
        "destination_username": request.destination.connection.username,
        "destination_provider_id": request.destination.provider_id,
        "destination_root_collection": request.destination.root_collection,
        "destination_root_mailbox": request.destination.root_collection,
        "collection_mapping": {
            key: request.folder_mapping[key] for key in sorted(request.folder_mapping)
        },
        "folder_mapping": {
            key: request.folder_mapping[key] for key in sorted(request.folder_mapping)
        },
        "pop3_destination_mailbox": request.options.pop3_destination_mailbox,
    }


def _sync_key_for_request(request: MigrationRequest) -> str:
    identity_payload = _request_sync_identity(request)
    encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class SQLiteStateStore:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize_schema()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self._db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    job_name TEXT,
                    status TEXT NOT NULL,
                    source_protocol TEXT NOT NULL,
                    destination_protocol TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    migrated_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    job_id TEXT NOT NULL,
                    source_mailbox TEXT NOT NULL,
                    last_source_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, source_mailbox),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sync_cursors (
                    sync_key TEXT NOT NULL,
                    source_mailbox TEXT NOT NULL,
                    last_source_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (sync_key, source_mailbox)
                );
                CREATE INDEX IF NOT EXISTS idx_sync_cursors_updated
                    ON sync_cursors(updated_at);
                CREATE TABLE IF NOT EXISTS message_migrations (
                    job_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    source_mailbox TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    destination_mailbox TEXT NOT NULL,
                    destination_id TEXT,
                    migrated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, fingerprint),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_message_migrations_job
                    ON message_migrations(job_id);
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_level TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_audit_events_job_created
                    ON audit_events(job_id, created_at);
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    batch_name TEXT,
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_items (
                    batch_id TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    job_id TEXT,
                    job_name TEXT,
                    source_username TEXT NOT NULL,
                    destination_username TEXT NOT NULL,
                    submit_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (batch_id, row_number),
                    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_batch_items_batch_row
                    ON batch_items(batch_id, row_number);
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    key_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_api_keys_user
                    ON api_keys(user_id);
                """
            )
        # Idempotent migration: add user_id column to jobs and batches
        for table in ("jobs", "batches"):
            try:
                with self._lock, self._connection() as connection:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
            except Exception:
                pass  # Column already exists

    def create_job(self, request: MigrationRequest, plan: MigrationPlan, user_id: str | None = None) -> str:
        job_id = str(uuid.uuid4())
        now = _utcnow_iso()
        request_json = json.dumps(request.to_dict(redact_password=True), sort_keys=True)
        plan_json = json.dumps(plan.to_dict(), sort_keys=True)
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id,
                    job_name,
                    status,
                    source_protocol,
                    destination_protocol,
                    request_json,
                    plan_json,
                    dry_run,
                    created_at,
                    updated_at,
                    user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.job_name,
                    JobStatus.PENDING.value,
                    request.source.protocol.value,
                    request.destination.protocol.value,
                    request_json,
                    plan_json,
                    1 if request.options.dry_run else 0,
                    now,
                    now,
                    user_id,
                ),
            )
        return job_id

    def has_job(self, job_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT 1 FROM jobs WHERE job_id = ?",
                (job_id,),
            )
            return cursor.fetchone() is not None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_jobs(self, *, limit: int = 20, user_id: str | None = None) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 500), 1)
        with self._lock, self._connection() as connection:
            if user_id is not None:
                cursor = connection.execute(
                    "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, safe_limit),
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def append_audit_event(
        self,
        job_id: str,
        event_type: str,
        *,
        event_level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        payload = payload or {}
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    job_id,
                    event_type,
                    event_level,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    event_type,
                    event_level,
                    json.dumps(payload, sort_keys=True),
                    _utcnow_iso(),
                ),
            )

    def list_audit_events(
        self,
        job_id: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 2000), 1)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT event_id, job_id, event_type, event_level, payload_json, created_at
                FROM audit_events
                WHERE job_id = ?
                ORDER BY event_id DESC
                LIMIT ?
                """,
                (job_id, safe_limit),
            )
            rows = cursor.fetchall()

        parsed_rows: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except json.JSONDecodeError:
                item.pop("payload_json", None)
                item["payload"] = {}
            parsed_rows.append(item)
        return parsed_rows

    def update_job_plan(self, job_id: str, plan: MigrationPlan) -> None:
        plan_json = json.dumps(plan.to_dict(), sort_keys=True)
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET plan_json = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (plan_json, _utcnow_iso(), job_id),
            )

    def set_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        last_error: str | None = None,
        set_started: bool = False,
        set_finished: bool = False,
    ) -> None:
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, _utcnow_iso()]
        if last_error is not None:
            updates.append("last_error = ?")
            params.append(last_error)
        if set_started:
            updates.append("started_at = ?")
            params.append(_utcnow_iso())
        if set_finished:
            updates.append("finished_at = ?")
            params.append(_utcnow_iso())
        params.append(job_id)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?",
                tuple(params),
            )

    def recover_stuck_jobs(self) -> int:
        """Mark jobs stuck in running state as failed. Returns count of recovered jobs."""
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?,
                    last_error = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE status = ?
                """,
                (
                    JobStatus.FAILED.value,
                    "Server restarted while job was running.",
                    _utcnow_iso(),
                    _utcnow_iso(),
                    JobStatus.RUNNING.value,
                ),
            )
            return cursor.rowcount

    def create_user(self, *, email: str, password_hash: str, is_admin: bool = False) -> str:
        user_id = str(uuid.uuid4())
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash, is_admin, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, email.lower().strip(), password_hash, 1 if is_admin else 0, now),
            )
        return user_id

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
        return dict(row) if row else None

    def count_users(self) -> int:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("SELECT COUNT(*) FROM users")
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT id, email, is_admin, created_at FROM users ORDER BY created_at ASC"
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def create_api_key(self, *, user_id: str, label: str = "") -> tuple[str, str]:
        key_id = str(uuid.uuid4())
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO api_keys (id, user_id, key_hash, label, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key_id, user_id, key_hash, label, now),
            )
        return key_id, raw_key

    def validate_api_key(self, raw_key: str) -> dict[str, Any] | None:
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT k.id AS key_id, u.id AS user_id, u.email, u.is_admin
                FROM api_keys k
                JOIN users u ON k.user_id = u.id
                WHERE k.key_hash = ?
                """,
                (key_hash,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (_utcnow_iso(), str(row["key_id"])),
            )
        return {
            "sub": str(row["user_id"]),
            "email": str(row["email"]),
            "is_admin": bool(row["is_admin"]),
        }

    def list_api_keys(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT id, label, created_at, last_used_at
                FROM api_keys WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def revoke_api_key(self, *, key_id: str, user_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM api_keys WHERE id = ? AND user_id = ?",
                (key_id, user_id),
            )
            return cursor.rowcount > 0

    def increment_counters(
        self,
        job_id: str,
        *,
        migrated: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET migrated_count = migrated_count + ?,
                    skipped_count = skipped_count + ?,
                    failed_count = failed_count + ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (migrated, skipped, failed, _utcnow_iso(), job_id),
            )

    def has_fingerprint(self, job_id: str, fingerprint: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT 1
                FROM message_migrations
                WHERE job_id = ? AND fingerprint = ?
                """,
                (job_id, fingerprint),
            )
            return cursor.fetchone() is not None

    def record_message(
        self,
        job_id: str,
        *,
        fingerprint: str,
        source_mailbox: str,
        source_id: str,
        destination_mailbox: str,
        destination_id: str | None,
    ) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO message_migrations (
                    job_id,
                    fingerprint,
                    source_mailbox,
                    source_id,
                    destination_mailbox,
                    destination_id,
                    migrated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    fingerprint,
                    source_mailbox,
                    source_id,
                    destination_mailbox,
                    destination_id,
                    _utcnow_iso(),
                ),
            )
            return cursor.rowcount > 0

    def set_checkpoint(self, job_id: str, source_mailbox: str, source_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints (job_id, source_mailbox, last_source_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id, source_mailbox)
                DO UPDATE SET
                    last_source_id = excluded.last_source_id,
                    updated_at = excluded.updated_at
                """,
                (job_id, source_mailbox, source_id, _utcnow_iso()),
            )

    def get_checkpoint(self, job_id: str, source_mailbox: str) -> str | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT last_source_id
                FROM checkpoints
                WHERE job_id = ? AND source_mailbox = ?
                """,
                (job_id, source_mailbox),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return str(row["last_source_id"]) if row["last_source_id"] is not None else None

    def build_sync_key(self, request: MigrationRequest) -> str:
        return _sync_key_for_request(request)

    def get_job_checkpoints(self, job_id: str) -> dict[str, str]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT source_mailbox, last_source_id
                FROM checkpoints
                WHERE job_id = ? AND last_source_id IS NOT NULL
                ORDER BY source_mailbox ASC
                """,
                (job_id,),
            )
            rows = cursor.fetchall()
        return {
            str(row["source_mailbox"]): str(row["last_source_id"])
            for row in rows
            if row["last_source_id"] is not None
        }

    def set_sync_cursor(self, *, sync_key: str, source_mailbox: str, source_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sync_cursors (sync_key, source_mailbox, last_source_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sync_key, source_mailbox)
                DO UPDATE SET
                    last_source_id = excluded.last_source_id,
                    updated_at = excluded.updated_at
                """,
                (sync_key, source_mailbox, source_id, _utcnow_iso()),
            )

    def get_sync_cursor(self, *, sync_key: str, source_mailbox: str) -> str | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT last_source_id
                FROM sync_cursors
                WHERE sync_key = ? AND source_mailbox = ?
                """,
                (sync_key, source_mailbox),
            )
            row = cursor.fetchone()
        if not row or row["last_source_id"] is None:
            return None
        return str(row["last_source_id"])

    def list_sync_cursors(self, *, sync_key: str) -> dict[str, str]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT source_mailbox, last_source_id
                FROM sync_cursors
                WHERE sync_key = ?
                ORDER BY source_mailbox ASC
                """,
                (sync_key,),
            )
            rows = cursor.fetchall()
        return {
            str(row["source_mailbox"]): str(row["last_source_id"])
            for row in rows
            if row["last_source_id"] is not None
        }

    def resolve_incremental_cursors(
        self,
        request: MigrationRequest,
        *,
        base_job_id: str | None = None,
    ) -> dict[str, str]:
        if base_job_id:
            base_job_row = self.get_job(base_job_id)
            if not base_job_row:
                raise ValueError(f"Base job {base_job_id} does not exist.")
            if str(base_job_row.get("status")) != JobStatus.COMPLETED.value:
                raise ValueError(
                    f"Base job {base_job_id} is not completed; incremental sync requires a completed base job."
                )
            return self.get_job_checkpoints(base_job_id)
        sync_key = self.build_sync_key(request)
        return self.list_sync_cursors(sync_key=sync_key)

    def update_sync_cursors_from_job(self, request: MigrationRequest, *, job_id: str) -> None:
        sync_key = self.build_sync_key(request)
        checkpoints = self.get_job_checkpoints(job_id)
        for source_mailbox, source_id in checkpoints.items():
            self.set_sync_cursor(
                sync_key=sync_key,
                source_mailbox=source_mailbox,
                source_id=source_id,
            )

    def create_batch(self, *, batch_name: str | None, total_rows: int, user_id: str | None = None) -> str:
        batch_id = str(uuid.uuid4())
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO batches (
                    batch_id,
                    batch_name,
                    total_rows,
                    created_at,
                    updated_at,
                    user_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    batch_name,
                    max(int(total_rows), 0),
                    now,
                    now,
                    user_id,
                ),
            )
        return batch_id

    def has_batch(self, batch_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT 1 FROM batches WHERE batch_id = ?",
                (batch_id,),
            )
            return cursor.fetchone() is not None

    def add_batch_item(
        self,
        *,
        batch_id: str,
        row_number: int,
        source_username: str,
        destination_username: str,
        job_id: str | None = None,
        job_name: str | None = None,
        submit_error: str | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO batch_items (
                    batch_id,
                    row_number,
                    job_id,
                    job_name,
                    source_username,
                    destination_username,
                    submit_error,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    int(row_number),
                    job_id,
                    job_name,
                    source_username,
                    destination_username,
                    submit_error,
                    _utcnow_iso(),
                    _utcnow_iso(),
                ),
            )

    def _get_batch_base_row(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (batch_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _list_batch_base_rows(self, *, limit: int = 20, user_id: str | None = None) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 200), 1)
        with self._lock, self._connection() as connection:
            if user_id is not None:
                cursor = connection.execute(
                    "SELECT * FROM batches WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, safe_limit),
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def _batch_summary_counts(self, batch_id: str) -> dict[str, int]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT
                    COUNT(bi.row_number) AS total_rows_actual,
                    SUM(
                        CASE
                            WHEN bi.submit_error IS NULL
                                 AND (j.job_id IS NULL OR j.status = ?)
                            THEN 1 ELSE 0
                        END
                    ) AS pending_rows,
                    SUM(CASE WHEN j.status = ? THEN 1 ELSE 0 END) AS running_rows,
                    SUM(CASE WHEN j.status = ? THEN 1 ELSE 0 END) AS completed_rows,
                    SUM(
                        CASE
                            WHEN bi.submit_error IS NOT NULL OR j.status = ?
                            THEN 1 ELSE 0
                        END
                    ) AS failed_rows,
                    SUM(COALESCE(j.migrated_count, 0)) AS migrated_count,
                    SUM(COALESCE(j.skipped_count, 0)) AS skipped_count,
                    SUM(COALESCE(j.failed_count, 0)) AS message_failed_count
                FROM batch_items bi
                LEFT JOIN jobs j ON j.job_id = bi.job_id
                WHERE bi.batch_id = ?
                """,
                (
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    batch_id,
                ),
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "total_rows_actual": 0,
                    "pending_rows": 0,
                    "running_rows": 0,
                    "completed_rows": 0,
                    "failed_rows": 0,
                    "migrated_count": 0,
                    "skipped_count": 0,
                    "message_failed_count": 0,
                }
            return {
                "total_rows_actual": int(row["total_rows_actual"] or 0),
                "pending_rows": int(row["pending_rows"] or 0),
                "running_rows": int(row["running_rows"] or 0),
                "completed_rows": int(row["completed_rows"] or 0),
                "failed_rows": int(row["failed_rows"] or 0),
                "migrated_count": int(row["migrated_count"] or 0),
                "skipped_count": int(row["skipped_count"] or 0),
                "message_failed_count": int(row["message_failed_count"] or 0),
            }

    def _batch_summary_row(self, base_row: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(base_row["batch_id"])
        counts = self._batch_summary_counts(batch_id)
        total_rows = int(base_row.get("total_rows") or counts["total_rows_actual"])
        status = derive_batch_status(
            total_rows=total_rows,
            pending_rows=counts["pending_rows"],
            running_rows=counts["running_rows"],
            completed_rows=counts["completed_rows"],
            failed_rows=counts["failed_rows"],
        )
        return {
            "batch_id": batch_id,
            "batch_name": base_row.get("batch_name"),
            "status": status,
            "total_rows": total_rows,
            "pending_rows": counts["pending_rows"],
            "running_rows": counts["running_rows"],
            "completed_rows": counts["completed_rows"],
            "failed_rows": counts["failed_rows"],
            "migrated_count": counts["migrated_count"],
            "skipped_count": counts["skipped_count"],
            "message_failed_count": counts["message_failed_count"],
            "created_at": base_row.get("created_at"),
            "updated_at": base_row.get("updated_at"),
        }

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        base_row = self._get_batch_base_row(batch_id)
        if not base_row:
            return None
        return self._batch_summary_row(base_row)

    def list_batches(self, *, limit: int = 20, user_id: str | None = None) -> list[dict[str, Any]]:
        base_rows = self._list_batch_base_rows(limit=limit, user_id=user_id)
        return [self._batch_summary_row(base_row) for base_row in base_rows]

    def list_batch_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT
                    bi.batch_id,
                    bi.row_number,
                    bi.job_id,
                    bi.job_name,
                    bi.source_username,
                    bi.destination_username,
                    bi.submit_error,
                    bi.created_at AS item_created_at,
                    bi.updated_at AS item_updated_at,
                    j.status AS job_status,
                    j.migrated_count,
                    j.skipped_count,
                    j.failed_count,
                    j.last_error,
                    j.created_at AS job_created_at,
                    j.updated_at AS job_updated_at,
                    j.started_at,
                    j.finished_at
                FROM batch_items bi
                LEFT JOIN jobs j ON j.job_id = bi.job_id
                WHERE bi.batch_id = ?
                ORDER BY bi.row_number ASC
                """,
                (batch_id,),
            )
            rows = cursor.fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            submit_error = row["submit_error"]
            job_status = row["job_status"]
            if submit_error:
                status = JobStatus.FAILED.value
            elif job_status:
                status = str(job_status)
            else:
                status = JobStatus.PENDING.value
            items.append(
                {
                    "batch_id": row["batch_id"],
                    "row_number": int(row["row_number"]),
                    "job_id": row["job_id"],
                    "job_name": row["job_name"],
                    "source_username": row["source_username"],
                    "destination_username": row["destination_username"],
                    "status": status,
                    "migrated_count": int(row["migrated_count"] or 0),
                    "skipped_count": int(row["skipped_count"] or 0),
                    "failed_count": int(row["failed_count"] or 0),
                    "last_error": submit_error or row["last_error"],
                    "created_at": row["job_created_at"] or row["item_created_at"],
                    "updated_at": row["job_updated_at"] or row["item_updated_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                }
            )
        return items
