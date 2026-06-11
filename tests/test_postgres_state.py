"""Tests for PostgreSQL state store adapter (Phase 12).

Integration tests that need a real PostgreSQL instance are skipped unless
POSTGRES_TEST_URL is set:

    POSTGRES_TEST_URL=postgresql://user:pass@localhost/test_db \\
        PYTHONPATH=src python3 -m unittest tests/test_postgres_state.py
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from groupware_migrator.engine.postgres_state import (
    PostgresStateStore,
    _translate_sql,
    create_state_store,
)
from groupware_migrator.engine.state import SQLiteStateStore


# ---------------------------------------------------------------------------
# Unit tests — SQL translation
# ---------------------------------------------------------------------------

class TestTranslateSql(unittest.TestCase):
    def test_question_mark_replaced(self):
        result = _translate_sql("SELECT * FROM t WHERE id = ?")
        self.assertEqual(result, "SELECT * FROM t WHERE id = %s")

    def test_multiple_question_marks(self):
        result = _translate_sql("INSERT INTO t (a, b) VALUES (?, ?)")
        self.assertIn("%s", result)
        self.assertEqual(result.count("%s"), 2)
        self.assertNotIn("?", result)

    def test_no_placeholders_unchanged(self):
        sql = "SELECT COUNT(*) FROM jobs"
        self.assertEqual(_translate_sql(sql), sql)

    def test_insert_or_ignore_becomes_on_conflict(self):
        sql = "INSERT OR IGNORE INTO t (a) VALUES (?)"
        result = _translate_sql(sql)
        self.assertIn("ON CONFLICT DO NOTHING", result)
        self.assertNotIn("OR IGNORE", result)
        self.assertIn("INSERT INTO", result)

    def test_insert_or_ignore_case_insensitive(self):
        sql = "insert or ignore into t (a) values (?)"
        result = _translate_sql(sql)
        self.assertIn("ON CONFLICT DO NOTHING", result)

    def test_plain_insert_no_conflict_added(self):
        sql = "INSERT INTO t (a) VALUES (?)"
        result = _translate_sql(sql)
        self.assertNotIn("ON CONFLICT DO NOTHING", result)

    def test_coalesce_and_aggregates_pass_through(self):
        sql = "SELECT COALESCE(SUM(migrated_count), 0) FROM jobs WHERE status = ?"
        result = _translate_sql(sql)
        self.assertIn("COALESCE", result)
        self.assertIn("%s", result)


# ---------------------------------------------------------------------------
# Unit tests — create_state_store factory
# ---------------------------------------------------------------------------

class TestCreateStateStore(unittest.TestCase):
    def test_returns_sqlite_without_database_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = create_state_store(state_db_path=str(Path(tmp) / "state.db"))
            self.assertIsInstance(store, SQLiteStateStore)

    def test_sqlite_created_at_given_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.db"
            create_state_store(state_db_path=str(path))
            self.assertTrue(path.exists())

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost/db"})
    @patch("groupware_migrator.engine.postgres_state.PostgresStateStore.__init__", return_value=None)
    def test_database_url_env_creates_postgres_store(self, mock_init):
        store = create_state_store(state_db_path="unused.db")
        self.assertIsInstance(store, PostgresStateStore)
        mock_init.assert_called_once_with("postgresql://user:pass@localhost/db")

    @patch("groupware_migrator.engine.postgres_state.PostgresStateStore.__init__", return_value=None)
    def test_explicit_database_url_creates_postgres_store(self, mock_init):
        store = create_state_store(database_url="postgresql://u:p@h/d")
        self.assertIsInstance(store, PostgresStateStore)

    @patch.dict(os.environ, {}, clear=True)
    def test_no_database_url_returns_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = create_state_store(state_db_path=str(Path(tmp) / "s.db"))
            self.assertIsInstance(store, SQLiteStateStore)
            self.assertNotIsInstance(store, PostgresStateStore)


# ---------------------------------------------------------------------------
# Unit tests — PostgresStateStore with mocked psycopg2
# ---------------------------------------------------------------------------

def _mock_psycopg2():
    """Return a mock psycopg2 module suitable for patching."""
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_cursor.fetchone.return_value = {}
    mock_cursor.fetchall.return_value = []

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn
    mock_pool.putconn = MagicMock()

    mock_psycopg2_pool = MagicMock()
    mock_psycopg2_pool.ThreadedConnectionPool.return_value = mock_pool

    mock_psycopg2_extras = MagicMock()

    return mock_conn, mock_cursor, mock_pool, mock_psycopg2_pool, mock_psycopg2_extras


class TestPostgresStateStoreMocked(unittest.TestCase):
    def test_import_error_raised_without_psycopg2(self):
        with patch.dict("sys.modules", {"psycopg2": None, "psycopg2.pool": None, "psycopg2.extras": None}):
            with self.assertRaises((ImportError, TypeError)):
                PostgresStateStore("postgresql://u:p@h/d")

    def test_checkpoint_wal_is_noop(self):
        store = PostgresStateStore.__new__(PostgresStateStore)
        store.checkpoint_wal()  # Must not raise

    def test_db_path_raises_not_implemented(self):
        store = PostgresStateStore.__new__(PostgresStateStore)
        with self.assertRaises(NotImplementedError):
            _ = store.db_path

    def test_translate_sql_called_on_execute(self):
        from groupware_migrator.engine.postgres_state import _PsycoWrapper
        mock_cursor = MagicMock()
        wrapper = _PsycoWrapper.__new__(_PsycoWrapper)
        wrapper._cur = mock_cursor
        wrapper.execute("SELECT * FROM t WHERE id = ?", (1,))
        called_sql = mock_cursor.execute.call_args[0][0]
        self.assertIn("%s", called_sql)
        self.assertNotIn("?", called_sql)

    def test_pysco_wrapper_executemany_translates(self):
        from groupware_migrator.engine.postgres_state import _PsycoWrapper
        mock_cursor = MagicMock()
        wrapper = _PsycoWrapper.__new__(_PsycoWrapper)
        wrapper._cur = mock_cursor
        wrapper.executemany("INSERT INTO t (a) VALUES (?)", [(1,), (2,)])
        called_sql = mock_cursor.executemany.call_args[0][0]
        self.assertIn("%s", called_sql)

    def test_pysco_wrapper_executescript_is_noop(self):
        from groupware_migrator.engine.postgres_state import _PsycoWrapper
        wrapper = _PsycoWrapper.__new__(_PsycoWrapper)
        wrapper.executescript("ANY SQL")  # Must not raise


# ---------------------------------------------------------------------------
# Integration tests — real PostgreSQL (skipped without POSTGRES_TEST_URL)
# ---------------------------------------------------------------------------

_POSTGRES_URL = os.environ.get("POSTGRES_TEST_URL", "")


@unittest.skipUnless(_POSTGRES_URL, "Set POSTGRES_TEST_URL to run PostgreSQL integration tests")
class TestPostgresIntegration(unittest.TestCase):
    def setUp(self):
        self.store = PostgresStateStore(_POSTGRES_URL)

    def test_create_and_get_user(self):
        from groupware_migrator.engine.state import hash_password
        user_id = self.store.create_user(
            email=f"pg-test-{os.urandom(4).hex()}@example.com",
            password_hash=hash_password("pass"),
            is_admin=False,
        )
        user = self.store.get_user_by_id(user_id)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], user_id)

    def test_system_stats_returns_dict(self):
        stats = self.store.system_stats()
        self.assertIn("users_total", stats)
        self.assertIn("jobs_total", stats)

    def test_create_oidc_provider(self):
        pid = self.store.create_oidc_provider(
            name="PG Test IdP",
            client_id="cid",
            client_secret="sec",
            issuer="https://idp.example.com",
        )
        row = self.store.get_oidc_provider(pid)
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "PG Test IdP")

    def test_export_state(self):
        data = self.store.export_state()
        self.assertIn("users", data)
        self.assertIn("jobs", data)


if __name__ == "__main__":
    unittest.main()
