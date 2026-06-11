from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
                CREATE TABLE IF NOT EXISTS admin_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_id TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                    ON admin_audit_events(created_at);
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    schedule_type TEXT NOT NULL DEFAULT 'cron',
                    schedule_expr TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_run_job_id TEXT,
                    last_run_status TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
                    ON scheduled_jobs(next_run_at, is_active);
                CREATE TABLE IF NOT EXISTS webhooks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    label TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    secret TEXT NOT NULL,
                    events_json TEXT NOT NULL DEFAULT '["job.completed","job.failed"]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_delivery_at TEXT,
                    last_delivery_status INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_webhooks_user
                    ON webhooks(user_id);
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    webhook_id TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    response_status INTEGER,
                    error TEXT,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    delivered_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_hook
                    ON webhook_deliveries(webhook_id, delivered_at);
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS org_memberships (
                    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (org_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS notification_prefs (
                    user_id      TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    on_completed INTEGER NOT NULL DEFAULT 0,
                    on_failed    INTEGER NOT NULL DEFAULT 0,
                    on_cancelled INTEGER NOT NULL DEFAULT 0
                );
                """
            )
        # Idempotent migrations
        for table in ("jobs", "batches"):
            try:
                with self._lock, self._connection() as connection:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
            except Exception:
                pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE jobs ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
        except Exception:
            pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'operator'")
        except Exception:
            pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
        except Exception:
            pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            with self._lock, self._connection() as connection:
                connection.execute("ALTER TABLE users ADD COLUMN totp_recovery_json TEXT")
        except Exception:
            pass

    def create_job(
        self,
        request: MigrationRequest,
        plan: MigrationPlan,
        user_id: str | None = None,
        priority: str = "normal",
    ) -> str:
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
                    user_id,
                    priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    priority,
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

    def increment_retry_count(self, job_id: str, retry_attempt: int) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE jobs SET retry_count = ?, status = ?, updated_at = ? WHERE job_id = ?",
                (retry_attempt, JobStatus.PENDING.value, _utcnow_iso(), job_id),
            )

    def cancel_job(self, job_id: str) -> bool:
        """Mark a pending job cancelled. Returns True if the job was updated."""
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = ?, finished_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.CANCELLED.value,
                    _utcnow_iso(),
                    _utcnow_iso(),
                    job_id,
                    JobStatus.PENDING.value,
                ),
            )
            return cursor.rowcount > 0

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        is_admin: bool = False,
        role: str = "operator",
    ) -> str:
        user_id = str(uuid.uuid4())
        now = _utcnow_iso()
        effective_role = role if role else ("admin" if is_admin else "operator")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash, is_admin, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, email.lower().strip(), password_hash, 1 if is_admin else 0, effective_role, now),
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

    def update_user(
        self,
        user_id: str,
        *,
        is_admin: bool | None = None,
        is_active: bool | None = None,
        role: str | None = None,
    ) -> bool:
        updates = []
        params: list[Any] = []
        if is_admin is not None:
            updates.append("is_admin = ?")
            params.append(1 if is_admin else 0)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        if role is not None:
            updates.append("role = ?")
            params.append(role)
            # Keep is_admin in sync with role for backward compat
            if role in ("admin", "super_admin") and is_admin is None:
                updates.append("is_admin = ?")
                params.append(1)
            elif role not in ("admin", "super_admin") and is_admin is None:
                updates.append("is_admin = ?")
                params.append(0)
        if not updates:
            return False
        params.append(user_id)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            return cursor.rowcount > 0

    def change_password(self, user_id: str, new_password_hash: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_password_hash, user_id),
            )
            return cursor.rowcount > 0

    def get_notification_prefs(self, user_id: str) -> dict[str, bool]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT on_completed, on_failed, on_cancelled FROM notification_prefs WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return {"on_completed": False, "on_failed": False, "on_cancelled": False}
        return {
            "on_completed": bool(row["on_completed"]),
            "on_failed": bool(row["on_failed"]),
            "on_cancelled": bool(row["on_cancelled"]),
        }

    def set_notification_prefs(
        self,
        user_id: str,
        *,
        on_completed: bool,
        on_failed: bool,
        on_cancelled: bool,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO notification_prefs (user_id, on_completed, on_failed, on_cancelled)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    on_completed = excluded.on_completed,
                    on_failed    = excluded.on_failed,
                    on_cancelled = excluded.on_cancelled
                """,
                (user_id, 1 if on_completed else 0, 1 if on_failed else 0, 1 if on_cancelled else 0),
            )

    def log_admin_action(
        self,
        *,
        admin_id: str,
        action: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO admin_audit_events (admin_id, action, target_id, details_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (admin_id, action, target_id, json.dumps(details or {}, sort_keys=True), _utcnow_iso()),
            )

    def list_admin_audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 500), 1)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT id, admin_id, action, target_id, details_json, created_at
                FROM admin_audit_events ORDER BY id DESC LIMIT ?
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except json.JSONDecodeError:
                item.pop("details_json", None)
                item["details"] = {}
            result.append(item)
        return result

    def system_stats(self) -> dict[str, Any]:
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self._lock, self._connection() as connection:
            users_total = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            jobs_total = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            jobs_running = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
            ).fetchone()[0]
            jobs_completed = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'completed'"
            ).fetchone()[0]
            jobs_failed = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'failed'"
            ).fetchone()[0]
            jobs_7d = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE created_at > ?", (cutoff_7d,)
            ).fetchone()[0]
            completed_7d = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'completed' AND created_at > ?",
                (cutoff_7d,),
            ).fetchone()[0]
            jobs_30d = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE created_at > ?", (cutoff_30d,)
            ).fetchone()[0]
            completed_30d = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'completed' AND created_at > ?",
                (cutoff_30d,),
            ).fetchone()[0]
            items_migrated = connection.execute(
                "SELECT COALESCE(SUM(migrated_count), 0) FROM jobs"
            ).fetchone()[0]
            batches_total = connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
        return {
            "users_total": int(users_total),
            "jobs_total": int(jobs_total),
            "jobs_running": int(jobs_running),
            "jobs_completed": int(jobs_completed),
            "jobs_failed": int(jobs_failed),
            "jobs_last_7d": int(jobs_7d),
            "success_rate_7d_pct": round(completed_7d / jobs_7d * 100) if jobs_7d > 0 else 0,
            "jobs_last_30d": int(jobs_30d),
            "success_rate_30d_pct": round(completed_30d / jobs_30d * 100) if jobs_30d > 0 else 0,
            "items_migrated_total": int(items_migrated),
            "batches_total": int(batches_total),
        }

    def cleanup_old_records(self, *, older_than_days: int) -> dict[str, int]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(older_than_days, 1))).isoformat()
        with self._lock, self._connection() as connection:
            jobs_cursor = connection.execute(
                """
                DELETE FROM jobs WHERE status IN ('completed', 'failed', 'cancelled')
                AND finished_at IS NOT NULL AND finished_at < ?
                """,
                (cutoff,),
            )
            batches_cursor = connection.execute(
                "DELETE FROM batches WHERE updated_at < ?",
                (cutoff,),
            )
            events_cursor = connection.execute(
                "DELETE FROM admin_audit_events WHERE created_at < ?",
                (cutoff,),
            )
        return {
            "jobs_deleted": jobs_cursor.rowcount,
            "batches_deleted": batches_cursor.rowcount,
            "admin_events_deleted": events_cursor.rowcount,
        }

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

    # ─── Scheduled jobs ────────────────────────────────────────────────────────

    def create_schedule(
        self,
        *,
        name: str,
        schedule_type: str,
        schedule_expr: str,
        request_json: str,
        next_run_at: str,
        user_id: str | None = None,
    ) -> str:
        schedule_id = str(uuid.uuid4())
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_jobs
                    (id, name, schedule_type, schedule_expr, request_json, next_run_at, user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (schedule_id, name, schedule_type, schedule_expr, request_json, next_run_at, user_id, now, now),
            )
        return schedule_id

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM scheduled_jobs WHERE id = ?", (schedule_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_schedules(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            if user_id is not None:
                cursor = connection.execute(
                    "SELECT * FROM scheduled_jobs WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM scheduled_jobs ORDER BY created_at DESC"
                )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def update_schedule(
        self,
        schedule_id: str,
        *,
        name: str | None = None,
        schedule_expr: str | None = None,
        is_active: bool | None = None,
        next_run_at: str | None = None,
    ) -> bool:
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [_utcnow_iso()]
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if schedule_expr is not None:
            updates.append("schedule_expr = ?")
            params.append(schedule_expr)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        if next_run_at is not None:
            updates.append("next_run_at = ?")
            params.append(next_run_at)
        params.append(schedule_id)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE scheduled_jobs SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            return cursor.rowcount > 0

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM scheduled_jobs WHERE id = ?", (schedule_id,)
            )
            return cursor.rowcount > 0

    def list_due_schedules(self, *, before: str) -> list[dict[str, Any]]:
        """Return active schedules whose next_run_at is at or before `before`."""
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM scheduled_jobs WHERE is_active = 1 AND next_run_at <= ? ORDER BY next_run_at ASC",
                (before,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def update_schedule_after_fire(
        self,
        *,
        schedule_id: str,
        job_id: str,
        next_run_at: str,
    ) -> None:
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE scheduled_jobs
                SET last_run_at = ?, last_run_job_id = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, job_id, next_run_at, now, schedule_id),
            )

    # ─── Webhooks ──────────────────────────────────────────────────────────────

    def create_webhook(
        self,
        *,
        user_id: str | None,
        label: str,
        url: str,
        secret: str,
        events: list[str] | None = None,
    ) -> str:
        webhook_id = str(uuid.uuid4())
        events_json = json.dumps(events or ["job.completed", "job.failed"], sort_keys=True)
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO webhooks (id, user_id, label, url, secret, events_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (webhook_id, user_id, label, url, secret, events_json, now),
            )
        return webhook_id

    def get_webhook(self, webhook_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM webhooks WHERE id = ?", (webhook_id,)
            )
            row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["events"] = json.loads(item.pop("events_json"))
        except Exception:
            item["events"] = []
        return item

    def list_webhooks(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            if user_id is not None:
                cursor = connection.execute(
                    "SELECT * FROM webhooks WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM webhooks ORDER BY created_at DESC"
                )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["events"] = json.loads(item.pop("events_json"))
            except Exception:
                item["events"] = []
            result.append(item)
        return result

    def delete_webhook(self, webhook_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM webhooks WHERE id = ?", (webhook_id,)
            )
            return cursor.rowcount > 0

    def list_webhooks_for_event(
        self,
        *,
        event_type: str,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active webhooks subscribed to the given event type."""
        with self._lock, self._connection() as connection:
            if user_id is not None:
                cursor = connection.execute(
                    "SELECT * FROM webhooks WHERE is_active = 1 AND user_id = ?",
                    (user_id,),
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM webhooks WHERE is_active = 1"
                )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                events = json.loads(item.pop("events_json", "[]"))
            except Exception:
                events = []
            if event_type in events:
                item["events"] = events
                result.append(item)
        return result

    def append_webhook_delivery(
        self,
        *,
        webhook_id: str,
        event_type: str,
        payload_json: str,
        response_status: int | None,
        error: str | None,
        attempt: int,
    ) -> None:
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO webhook_deliveries
                    (webhook_id, event_type, payload_json, response_status, error, attempt, delivered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (webhook_id, event_type, payload_json, response_status, error, attempt, now),
            )
            connection.execute(
                "UPDATE webhooks SET last_delivery_at = ?, last_delivery_status = ? WHERE id = ?",
                (now, response_status, webhook_id),
            )

    def list_webhook_deliveries(
        self,
        webhook_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 200), 1)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT id, webhook_id, event_type, response_status, error, attempt, delivered_at
                FROM webhook_deliveries WHERE webhook_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (webhook_id, safe_limit),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    # ─── TOTP 2FA ──────────────────────────────────────────────────────────────

    def set_totp_secret(self, user_id: str, *, secret: str, recovery_codes: list[str]) -> None:
        """Store (unverified) TOTP secret and hashed recovery codes."""
        recovery_json = json.dumps(
            [hashlib.sha256(c.encode()).hexdigest() for c in recovery_codes],
            sort_keys=True,
        )
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE users SET totp_secret = ?, totp_recovery_json = ?, totp_enabled = 0 WHERE id = ?",
                (secret, recovery_json, user_id),
            )

    def enable_totp(self, user_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE users SET totp_enabled = 1 WHERE id = ? AND totp_secret IS NOT NULL",
                (user_id,),
            )
            return cursor.rowcount > 0

    def disable_totp(self, user_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE users SET totp_secret = NULL, totp_enabled = 0, totp_recovery_json = NULL WHERE id = ?",
                (user_id,),
            )
            return cursor.rowcount > 0

    def consume_totp_recovery_code(self, user_id: str, code: str) -> bool:
        """Use a recovery code (removes it). Returns True if valid."""
        user = self.get_user_by_id(user_id)
        if not user or not user.get("totp_recovery_json"):
            return False
        try:
            hashed_codes: list[str] = json.loads(user["totp_recovery_json"])
        except Exception:
            return False
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        if code_hash not in hashed_codes:
            return False
        hashed_codes.remove(code_hash)
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE users SET totp_recovery_json = ? WHERE id = ?",
                (json.dumps(hashed_codes), user_id),
            )
        return True

    # ─── Organizations ─────────────────────────────────────────────────────────

    def create_org(self, *, name: str, slug: str, created_by: str) -> str:
        org_id = str(uuid.uuid4())
        now = _utcnow_iso()
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO organizations (id, name, slug, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (org_id, name, slug.lower().strip(), created_by, now),
            )
            connection.execute(
                "INSERT INTO org_memberships (org_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
                (org_id, created_by, now),
            )
        return org_id

    def get_org(self, org_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (org_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def get_org_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM organizations WHERE slug = ?", (slug.lower().strip(),)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_orgs(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        """List orgs; admin (user_id=None) sees all."""
        with self._lock, self._connection() as connection:
            if user_id is not None:
                cursor = connection.execute(
                    """
                    SELECT o.* FROM organizations o
                    JOIN org_memberships m ON m.org_id = o.id
                    WHERE m.user_id = ?
                    ORDER BY o.created_at DESC
                    """,
                    (user_id,),
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM organizations ORDER BY created_at DESC"
                )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def list_org_members(self, org_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                SELECT u.id, u.email, u.role AS user_role, m.role AS org_role, m.joined_at
                FROM org_memberships m
                JOIN users u ON u.id = m.user_id
                WHERE m.org_id = ?
                ORDER BY m.joined_at ASC
                """,
                (org_id,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_org_member_role(self, org_id: str, user_id: str) -> str | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "SELECT role FROM org_memberships WHERE org_id = ? AND user_id = ?",
                (org_id, user_id),
            )
            row = cursor.fetchone()
        return str(row["role"]) if row else None

    def add_org_member(self, org_id: str, user_id: str, role: str = "member") -> bool:
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO org_memberships (org_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (org_id, user_id, role, _utcnow_iso()),
                )
            return True
        except Exception:
            return False

    def remove_org_member(self, org_id: str, user_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM org_memberships WHERE org_id = ? AND user_id = ?",
                (org_id, user_id),
            )
            return cursor.rowcount > 0

    def delete_org(self, org_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM organizations WHERE id = ?", (org_id,)
            )
            return cursor.rowcount > 0
