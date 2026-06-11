"""PostgreSQL-backed state store, opt-in via DATABASE_URL.

Drop-in replacement for SQLiteStateStore. Requires psycopg2:

    pip install "groupware-migrator[postgres]"
    DATABASE_URL=postgresql://user:pass@host:5432/db groupware-migrator ...

All core functionality is supported. WAL-checkpoint and SQLite file
download are not available; use pg_dump for PostgreSQL backups.
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from groupware_migrator.engine.state import SQLiteStateStore, _utcnow_iso


def _translate_sql(sql: str) -> str:
    """Convert SQLite-flavoured SQL to psycopg2-compatible SQL."""
    # ? → %s (parameter placeholder)
    sql = sql.replace("?", "%s")
    # INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING
    if re.search(r"\bINSERT\s+OR\s+IGNORE\b", sql, re.IGNORECASE):
        sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", sql, flags=re.IGNORECASE)
        sql = sql.rstrip().rstrip(";") + "\nON CONFLICT DO NOTHING"
    return sql


class _PsycoWrapper:
    """Wraps a psycopg2 connection to match the sqlite3 interface used by SQLiteStateStore."""

    def __init__(self, conn: Any) -> None:
        import psycopg2.extras  # noqa: PLC0415
        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # -- DML / DQL ----------------------------------------------------------

    def execute(self, sql: str, params: tuple | list = ()) -> Any:
        self._cur.execute(_translate_sql(sql), params or None)
        return self._cur

    def executemany(self, sql: str, params_list: list) -> None:
        self._cur.executemany(_translate_sql(sql), params_list)

    def executescript(self, sql: str) -> None:
        """No-op: PostgresStateStore._initialize_schema() never calls this."""

    # -- Cursor pass-through ------------------------------------------------

    def fetchone(self) -> dict | None:
        return self._cur.fetchone()

    def fetchall(self) -> list[dict]:
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


# PostgreSQL DDL — same schema as SQLite but with BIGSERIAL and no PRAGMA.
_SCHEMA_STATEMENTS: list[str] = [
    """CREATE TABLE IF NOT EXISTS jobs (
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
    )""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
        job_id TEXT NOT NULL,
        source_mailbox TEXT NOT NULL,
        last_source_id TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (job_id, source_mailbox),
        FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS sync_cursors (
        sync_key TEXT NOT NULL,
        source_mailbox TEXT NOT NULL,
        last_source_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (sync_key, source_mailbox)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sync_cursors_updated ON sync_cursors(updated_at)",
    """CREATE TABLE IF NOT EXISTS message_migrations (
        job_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        source_mailbox TEXT NOT NULL,
        source_id TEXT NOT NULL,
        destination_mailbox TEXT NOT NULL,
        destination_id TEXT,
        migrated_at TEXT NOT NULL,
        PRIMARY KEY (job_id, fingerprint),
        FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX IF NOT EXISTS idx_message_migrations_job ON message_migrations(job_id)",
    """CREATE TABLE IF NOT EXISTS audit_events (
        event_id BIGSERIAL PRIMARY KEY,
        job_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        event_level TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_events_job_created ON audit_events(job_id, created_at)",
    """CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY,
        batch_name TEXT,
        total_rows INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS batch_items (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_batch_items_batch_row ON batch_items(batch_id, row_number)",
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS api_keys (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key_hash TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_used_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)",
    """CREATE TABLE IF NOT EXISTS admin_audit_events (
        id BIGSERIAL PRIMARY KEY,
        admin_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target_id TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_events(created_at)",
    """CREATE TABLE IF NOT EXISTS scheduled_jobs (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run ON scheduled_jobs(next_run_at, is_active)",
    """CREATE TABLE IF NOT EXISTS webhooks (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_webhooks_user ON webhooks(user_id)",
    """CREATE TABLE IF NOT EXISTS webhook_deliveries (
        id BIGSERIAL PRIMARY KEY,
        webhook_id TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        response_status INTEGER,
        error TEXT,
        attempt INTEGER NOT NULL DEFAULT 1,
        delivered_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_hook ON webhook_deliveries(webhook_id, delivered_at)",
    """CREATE TABLE IF NOT EXISTS organizations (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS org_memberships (
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'member',
        joined_at TEXT NOT NULL,
        PRIMARY KEY (org_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS notification_prefs (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        on_completed INTEGER NOT NULL DEFAULT 0,
        on_failed INTEGER NOT NULL DEFAULT 0,
        on_cancelled INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS oidc_providers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        client_id TEXT NOT NULL,
        client_secret TEXT NOT NULL,
        issuer TEXT NOT NULL,
        discovery_url TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'openid email profile',
        admin_claim TEXT NOT NULL DEFAULT '',
        admin_claim_value TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
]

# Idempotent column additions — same as SQLite migrations but run via separate statements.
_MIGRATIONS: list[tuple[str, str]] = [
    ("jobs", "ALTER TABLE jobs ADD COLUMN user_id TEXT"),
    ("batches", "ALTER TABLE batches ADD COLUMN user_id TEXT"),
    ("users", "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"),
    ("jobs", "ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "ALTER TABLE jobs ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'"),
    ("users", "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'operator'"),
    ("users", "ALTER TABLE users ADD COLUMN totp_secret TEXT"),
    ("users", "ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0"),
    ("users", "ALTER TABLE users ADD COLUMN totp_recovery_json TEXT"),
    ("users", "ALTER TABLE users ADD COLUMN auth_backend TEXT NOT NULL DEFAULT 'local'"),
]


class PostgresStateStore(SQLiteStateStore):
    """PostgreSQL-backed alternative to SQLiteStateStore.

    Usage::

        from groupware_migrator.engine.postgres_state import PostgresStateStore
        store = PostgresStateStore("postgresql://user:pass@localhost/db")

    Or via ``DATABASE_URL`` environment variable (handled in ``create_state_store()``).
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._lock = threading.Lock()
        self._pool: Any = None
        self._make_pool()
        self._initialize_schema()

    def _make_pool(self) -> None:
        try:
            import psycopg2.pool  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for PostgreSQL support. "
                "Install it with: pip install 'groupware-migrator[postgres]'"
            ) from exc
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=10, dsn=self._database_url
        )

    @contextmanager
    def _connection(self):  # type: ignore[override]
        conn = self._pool.getconn()
        try:
            wrapper = _PsycoWrapper(conn)
            yield wrapper
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def _initialize_schema(self) -> None:
        with self._lock, self._connection() as connection:
            for stmt in _SCHEMA_STATEMENTS:
                connection.execute(stmt)
        # Idempotent column additions
        for _table, alter_sql in _MIGRATIONS:
            try:
                with self._lock, self._connection() as connection:
                    connection.execute(alter_sql)
            except Exception:
                pass

    # -- PostgreSQL-specific overrides --------------------------------------

    def checkpoint_wal(self) -> None:
        """No-op: PostgreSQL manages WAL internally."""

    @property
    def db_path(self) -> Path:
        raise NotImplementedError(
            "db_path is not available for the PostgreSQL backend. "
            "Use pg_dump for PostgreSQL backups."
        )

    def export_state(self) -> dict[str, Any]:
        """Same JSON export as SQLiteStateStore; no path/file needed."""
        return super().export_state()


def create_state_store(
    *,
    state_db_path: str | None = None,
    database_url: str | None = None,
) -> SQLiteStateStore:
    """Factory that returns a SQLiteStateStore or PostgresStateStore.

    Priority: explicit ``database_url`` > ``DATABASE_URL`` env var > ``state_db_path``.
    """
    import os  # noqa: PLC0415
    url = database_url or os.environ.get("DATABASE_URL", "")
    if url:
        return PostgresStateStore(url)
    path = state_db_path or "data/state.db"
    return SQLiteStateStore(Path(path))
