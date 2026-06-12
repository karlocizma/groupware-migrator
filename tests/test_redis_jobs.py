"""Tests for the Redis-backed job manager (Phase 12).

All tests use mocked Redis — no live Redis server required.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from groupware_migrator.engine.redis_jobs import (
    CANCEL_PREFIX,
    QUEUE_KEY,
    RUNNING_PREFIX,
    RedisJobManager,
    _CANCEL_TTL,
)
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import MigrationRequest


def _make_request() -> MigrationRequest:
    return MigrationRequest.from_dict(
        {
            "source": {
                "protocol": "imap",
                "connection": {
                    "host": "src.example.com",
                    "username": "u@src.example.com",
                    "password": "pass",
                },
            },
            "destination": {
                "protocol": "imap",
                "connection": {
                    "host": "dst.example.com",
                    "username": "u@dst.example.com",
                    "password": "pass",
                },
            },
        }
    )


def _make_mock_client() -> MagicMock:
    r = MagicMock()
    r.exists.return_value = 0
    r.rpush.return_value = 1
    r.setex.return_value = True
    r.delete.return_value = 1
    return r


def _mock_redis_module(client: MagicMock) -> MagicMock:
    """Return a fake `redis` module whose from_url() returns *client*."""
    mod = MagicMock()
    mod.from_url.return_value = client
    return mod


class TestRedisJobManagerInit(unittest.TestCase):
    def test_raises_import_error_without_redis(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStateStore(Path(tmp) / "s.db")
            with patch.dict("sys.modules", {"redis": None}):
                with self.assertRaises(ImportError):
                    RedisJobManager(state_store=store)

    def test_init_with_mocked_redis(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStateStore(Path(tmp) / "s.db")
            client = _make_mock_client()
            with patch.dict("sys.modules", {"redis": _mock_redis_module(client)}):
                mgr = RedisJobManager(state_store=store)
        self.assertIsNotNone(mgr)


class TestRedisJobManagerStartJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_start_job_creates_db_record(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        job = self.store.get_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "pending")

    def test_start_job_pushes_to_redis(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        self.mock_r.rpush.assert_called_once()
        queue_name, payload_str = self.mock_r.rpush.call_args[0]
        self.assertEqual(queue_name, QUEUE_KEY)
        payload = json.loads(payload_str)
        self.assertEqual(payload["job_id"], job_id)
        self.assertIn("request", payload)

    def test_start_job_payload_includes_db_path(self):
        request = _make_request()
        self.mgr.start_job(request)
        _, payload_str = self.mock_r.rpush.call_args[0]
        payload = json.loads(payload_str)
        self.assertIn("db_path", payload)
        self.assertIsNotNone(payload["db_path"])

    def test_start_job_returns_job_id_string(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        self.assertIsInstance(job_id, str)
        self.assertTrue(len(job_id) > 0)

    def test_start_job_raises_when_shutting_down(self):
        self.mgr.shutdown()
        with self.assertRaises(RuntimeError):
            self.mgr.start_job(_make_request())


class TestRedisJobManagerResumeJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_resume_job_enqueues_existing_job(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        self.mock_r.rpush.reset_mock()
        self.mgr.resume_job(request=request, job_id=job_id)
        self.mock_r.rpush.assert_called_once()

    def test_resume_job_raises_for_unknown_job(self):
        with self.assertRaises(ValueError):
            self.mgr.resume_job(request=_make_request(), job_id="nonexistent-id")

    def test_resume_job_raises_when_shutting_down(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        self.mgr.shutdown()
        with self.assertRaises(RuntimeError):
            self.mgr.resume_job(request=request, job_id=job_id)


class TestRedisJobManagerCancelJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_cancel_job_sets_redis_flag(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        self.mock_r.reset_mock()
        result = self.mgr.cancel_job(job_id)
        self.assertTrue(result)
        self.mock_r.setex.assert_called_once_with(
            f"{CANCEL_PREFIX}{job_id}", _CANCEL_TTL, "1"
        )

    def test_cancel_job_marks_db_cancelled(self):
        request = _make_request()
        job_id = self.mgr.start_job(request)
        self.mgr.cancel_job(job_id)
        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "cancelled")


class TestRedisJobManagerIsRunning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_is_running_false_when_no_redis_key(self):
        self.mock_r.exists.return_value = 0
        self.assertFalse(self.mgr.is_running("some-job-id"))

    def test_is_running_true_when_redis_key_set(self):
        self.mock_r.exists.return_value = 1
        self.assertTrue(self.mgr.is_running("some-job-id"))

    def test_is_running_checks_correct_key(self):
        self.mgr.is_running("job-42")
        self.mock_r.exists.assert_called_with(f"{RUNNING_PREFIX}job-42")


class TestRedisJobManagerRunningCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_running_count_zero_with_no_jobs(self):
        self.assertEqual(self.mgr.running_count(), 0)

    def test_running_count_reflects_db_running_jobs(self):
        from groupware_migrator.models import MigrationPlan, JobStatus  # noqa: PLC0415
        request = _make_request()
        job_id = self.store.create_job(request=request, plan=MigrationPlan())
        self.store.set_job_status(job_id, JobStatus.RUNNING)
        self.assertEqual(self.mgr.running_count(), 1)


class TestRedisJobManagerDrain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_drain_returns_zero_when_no_running_jobs(self):
        remaining = self.mgr.drain(timeout=1.0)
        self.assertEqual(remaining, 0)

    def test_drain_sets_accepting_false(self):
        self.mgr.drain(timeout=0.1)
        self.assertFalse(self.mgr._accepting)

    def test_drain_returns_nonzero_when_jobs_still_running(self):
        from groupware_migrator.models import MigrationPlan, JobStatus  # noqa: PLC0415
        request = _make_request()
        job_id = self.store.create_job(request=request, plan=MigrationPlan())
        self.store.set_job_status(job_id, JobStatus.RUNNING)
        remaining = self.mgr.drain(timeout=0.1)
        self.assertEqual(remaining, 1)


class TestRedisJobManagerShutdown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_shutdown_sets_accepting_false(self):
        self.mgr.shutdown()
        self.assertFalse(self.mgr._accepting)

    def test_shutdown_does_not_raise(self):
        self.mgr.shutdown(wait=True)
        self.mgr.shutdown(wait=False)


class TestRedisPayloadSerialisation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SQLiteStateStore(Path(self.tmp) / "s.db")
        self.mock_r = _make_mock_client()
        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            self.mgr = RedisJobManager(state_store=self.store)

    def test_payload_is_valid_json(self):
        request = _make_request()
        self.mgr.start_job(request)
        _, payload_str = self.mock_r.rpush.call_args[0]
        payload = json.loads(payload_str)
        self.assertIn("job_id", payload)
        self.assertIn("request", payload)

    def test_payload_request_roundtrips(self):
        request = _make_request()
        self.mgr.start_job(request)
        _, payload_str = self.mock_r.rpush.call_args[0]
        payload = json.loads(payload_str)
        restored = MigrationRequest.from_dict(payload["request"])
        self.assertEqual(restored.source.connection.host, request.source.connection.host)
        self.assertEqual(restored.destination.connection.host, request.destination.connection.host)

    def test_postgres_backend_uses_database_url(self):
        from groupware_migrator.engine.postgres_state import PostgresStateStore  # noqa: PLC0415

        store = PostgresStateStore.__new__(PostgresStateStore)
        store._database_url = "postgresql://u:p@h/d"
        store._lock = __import__("threading").Lock()

        with patch.dict("sys.modules", {"redis": _mock_redis_module(self.mock_r)}):
            mgr = RedisJobManager(state_store=store)


        with patch.object(store, "create_job", return_value="pg-job-1"):
            with patch.object(store, "has_job", return_value=True):
                request = _make_request()
                mgr.start_job(request)

        _, payload_str = self.mock_r.rpush.call_args[0]
        payload = json.loads(payload_str)
        self.assertIsNone(payload["db_path"])
        self.assertEqual(payload["database_url"], "postgresql://u:p@h/d")


if __name__ == "__main__":
    unittest.main()
