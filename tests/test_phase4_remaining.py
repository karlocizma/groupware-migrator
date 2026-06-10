"""Tests for remaining Phase 4 features: retry policy, drain, backup/restore CLI."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from groupware_migrator.cli import _backup_db, _restore_db
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.state import SQLiteStateStore, hash_password
from groupware_migrator.models import MigrationPlan, MigrationRequest
from groupware_migrator.models.domain import (
    ConnectionConfig,
    DestinationEndpoint,
    DestinationProtocol,
    JobStatus,
    MigrationOptions,
    SourceEndpoint,
    SourceProtocol,
    WorkloadType,
)


def _make_request(max_retries: int = 0) -> MigrationRequest:
    return MigrationRequest(
        source=SourceEndpoint(
            protocol=SourceProtocol.IMAP,
            connection=ConnectionConfig(host="src", port=993, username="u", password="p"),
        ),
        destination=DestinationEndpoint(
            protocol=DestinationProtocol.IMAP,
            connection=ConnectionConfig(host="dst", port=993, username="u", password="p"),
        ),
        workload=WorkloadType.MAIL,
        options=MigrationOptions(max_retries=max_retries),
    )


class TestMigrationOptionsRetry(unittest.TestCase):
    def test_default_max_retries_zero(self):
        opts = MigrationOptions()
        self.assertEqual(opts.max_retries, 0)

    def test_from_dict_max_retries(self):
        opts = MigrationOptions.from_dict({"max_retries": 3})
        self.assertEqual(opts.max_retries, 3)

    def test_from_dict_max_retries_negative_clamped(self):
        opts = MigrationOptions.from_dict({"max_retries": -1})
        self.assertEqual(opts.max_retries, 0)

    def test_to_dict_includes_max_retries(self):
        opts = MigrationOptions(max_retries=2)
        d = opts.to_dict()
        self.assertEqual(d["max_retries"], 2)


class TestBackgroundJobManagerDrain(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_drain_stops_accepting_new_jobs(self):
        runner = MagicMock()
        runner.run.return_value = MagicMock()
        mgr = BackgroundJobManager(state_store=self._store, runner=runner)
        mgr.drain(timeout=0.1)
        self.assertFalse(mgr._accepting)
        mgr.shutdown(wait=False)

    def test_drain_returns_zero_when_no_jobs(self):
        runner = MagicMock()
        mgr = BackgroundJobManager(state_store=self._store, runner=runner)
        still_running = mgr.drain(timeout=1.0)
        self.assertEqual(still_running, 0)
        mgr.shutdown(wait=False)

    def test_drain_waits_for_running_job(self):
        event = threading.Event()

        def slow_run(**kwargs):
            event.wait(timeout=2.0)
            return MagicMock()

        runner = MagicMock()
        runner.run.side_effect = slow_run
        mgr = BackgroundJobManager(state_store=self._store, runner=runner)

        request = _make_request()
        job_id = self._store.create_job(request, MigrationPlan())
        mgr._submit(job_id=job_id, request=request)

        # Job is running — drain should see 1 job initially
        self.assertEqual(mgr.running_count(), 1)

        # Release the job after a short delay
        threading.Timer(0.2, event.set).start()
        still_running = mgr.drain(timeout=2.0)
        self.assertEqual(still_running, 0)
        mgr.shutdown(wait=False)

    def test_drain_timeout_returns_nonzero_if_still_running(self):
        def hang(**kwargs):
            time.sleep(10)
            return MagicMock()

        runner = MagicMock()
        runner.run.side_effect = hang
        mgr = BackgroundJobManager(state_store=self._store, runner=runner)

        request = _make_request()
        job_id = self._store.create_job(request, MigrationPlan())
        mgr._submit(job_id=job_id, request=request)

        still_running = mgr.drain(timeout=0.3)
        self.assertGreater(still_running, 0)
        mgr.shutdown(wait=False)


class TestJobRetryPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_retry_scheduled_on_failed_job(self):
        retry_triggered = threading.Event()
        call_count = [0]

        def failing_then_ok(**kwargs):
            call_count[0] += 1
            job_id = kwargs.get("resume_job_id")
            if call_count[0] == 1:
                # First attempt: fail
                self._store.set_job_status(job_id, JobStatus.FAILED, set_finished=True)
            else:
                # Second attempt: succeed
                self._store.set_job_status(job_id, JobStatus.COMPLETED, set_finished=True)
                retry_triggered.set()
            return MagicMock()

        runner = MagicMock()
        runner.run.side_effect = failing_then_ok

        # Patch retry delay to 0 so test doesn't wait 30s
        import groupware_migrator.engine.background as bg_module
        original_base = bg_module._RETRY_BASE_DELAY
        bg_module._RETRY_BASE_DELAY = 0

        try:
            mgr = BackgroundJobManager(state_store=self._store, runner=runner)
            request = _make_request(max_retries=1)
            job_id = self._store.create_job(request, MigrationPlan())
            mgr._submit(job_id=job_id, request=request)

            triggered = retry_triggered.wait(timeout=5.0)
            self.assertTrue(triggered, "Retry was never triggered")
            self.assertEqual(call_count[0], 2)
        finally:
            bg_module._RETRY_BASE_DELAY = original_base
            mgr.shutdown(wait=False)

    def test_no_retry_when_max_retries_zero(self):
        call_count = [0]

        def always_fail(**kwargs):
            call_count[0] += 1
            job_id = kwargs.get("resume_job_id")
            self._store.set_job_status(job_id, JobStatus.FAILED, set_finished=True)
            return MagicMock()

        runner = MagicMock()
        runner.run.side_effect = always_fail
        mgr = BackgroundJobManager(state_store=self._store, runner=runner)

        request = _make_request(max_retries=0)
        job_id = self._store.create_job(request, MigrationPlan())
        mgr._submit(job_id=job_id, request=request)

        time.sleep(0.3)
        self.assertEqual(call_count[0], 1, "Should not retry when max_retries=0")
        mgr.shutdown(wait=False)

    def test_increment_retry_count_in_db(self):
        request = _make_request()
        job_id = self._store.create_job(request, MigrationPlan())
        self._store.increment_retry_count(job_id, 2)
        row = self._store.get_job(job_id)
        self.assertEqual(row["retry_count"], 2)
        self.assertEqual(row["status"], JobStatus.PENDING.value)


class TestBackupRestore(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "state.db"
        self._backup_path = Path(self._tmp.name) / "backup.db"
        # Create a database with some data
        store = SQLiteStateStore(self._db_path)
        store.create_user(email="admin@x.com", password_hash=hash_password("p"), is_admin=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_backup_creates_valid_sqlite_file(self):
        _backup_db(self._db_path, self._backup_path)
        self.assertTrue(self._backup_path.exists())
        conn = sqlite3.connect(str(self._backup_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(result, "ok")

    def test_backup_contains_data(self):
        _backup_db(self._db_path, self._backup_path)
        store2 = SQLiteStateStore(self._backup_path)
        self.assertEqual(store2.count_users(), 1)

    def test_restore_from_backup(self):
        _backup_db(self._db_path, self._backup_path)
        restore_path = Path(self._tmp.name) / "restored.db"
        _restore_db(self._backup_path, restore_path, force=False)
        store3 = SQLiteStateStore(restore_path)
        self.assertEqual(store3.count_users(), 1)

    def test_restore_refuses_to_overwrite_without_force(self):
        _backup_db(self._db_path, self._backup_path)
        with self.assertRaises(FileExistsError):
            _restore_db(self._backup_path, self._db_path, force=False)

    def test_restore_overwrites_with_force(self):
        _backup_db(self._db_path, self._backup_path)
        # Should not raise
        _restore_db(self._backup_path, self._db_path, force=True)

    def test_backup_raises_if_db_missing(self):
        with self.assertRaises(FileNotFoundError):
            _backup_db(Path(self._tmp.name) / "nonexistent.db", self._backup_path)

    def test_restore_raises_if_backup_missing(self):
        with self.assertRaises(FileNotFoundError):
            _restore_db(Path(self._tmp.name) / "missing.db", self._db_path, force=True)


if __name__ == "__main__":
    unittest.main()
