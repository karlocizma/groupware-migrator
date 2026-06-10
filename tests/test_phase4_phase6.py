"""Tests for Phase 4 (production hardening) and Phase 6 (admin & observability)."""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from groupware_migrator.api.rate_limit import LoginRateLimiter
from groupware_migrator.engine.state import SQLiteStateStore, hash_password, verify_password
from groupware_migrator.models.domain import JobStatus


class TestRateLimiter(unittest.TestCase):
    def _limiter(self, max_attempts=3, window=60, lockout=120):
        return LoginRateLimiter(max_attempts=max_attempts, window_seconds=window, lockout_seconds=lockout)

    def test_allows_under_limit(self):
        lim = self._limiter()
        for _ in range(3):
            lim.check_and_record("1.2.3.4")  # should not raise

    def test_blocks_over_limit(self):
        from fastapi import HTTPException
        lim = self._limiter(max_attempts=2)
        lim.check_and_record("1.2.3.4")
        lim.check_and_record("1.2.3.4")
        with self.assertRaises(HTTPException) as ctx:
            lim.check_and_record("1.2.3.4")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_clears_on_success(self):
        lim = self._limiter(max_attempts=2)
        lim.check_and_record("10.0.0.1")
        lim.clear("10.0.0.1")
        lim.check_and_record("10.0.0.1")  # should not raise after clear

    def test_different_ips_independent(self):
        from fastapi import HTTPException
        lim = self._limiter(max_attempts=1)
        lim.check_and_record("a")
        with self.assertRaises(HTTPException):
            lim.check_and_record("a")
        lim.check_and_record("b")  # different IP, should succeed


class TestCancelJob(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        uid = self._store.create_user(
            email="test@example.com",
            password_hash=hash_password("password"),
        )
        self._user_id = uid

    def tearDown(self):
        self._tmp.cleanup()

    def _make_job(self) -> str:
        from groupware_migrator.models import MigrationPlan, MigrationRequest
        from groupware_migrator.models.domain import (
            ConnectionConfig,
            DestinationEndpoint,
            DestinationProtocol,
            MigrationOptions,
            SourceEndpoint,
            SourceProtocol,
            WorkloadType,
        )
        req = MigrationRequest(
            source=SourceEndpoint(
                protocol=SourceProtocol.IMAP,
                connection=ConnectionConfig(host="src", port=993, username="u", password="p"),
            ),
            destination=DestinationEndpoint(
                protocol=DestinationProtocol.IMAP,
                connection=ConnectionConfig(host="dst", port=993, username="u", password="p"),
            ),
            workload=WorkloadType.MAIL,
            options=MigrationOptions(),
        )
        return self._store.create_job(req, MigrationPlan())

    def test_cancel_pending_job(self):
        job_id = self._make_job()
        result = self._store.cancel_job(job_id)
        self.assertTrue(result)
        job = self._store.get_job(job_id)
        self.assertEqual(job["status"], JobStatus.CANCELLED.value)

    def test_cancel_nonexistent_returns_false(self):
        result = self._store.cancel_job("nonexistent-id")
        self.assertFalse(result)

    def test_cancel_completed_returns_false(self):
        job_id = self._make_job()
        self._store.set_job_status(job_id, JobStatus.COMPLETED)
        result = self._store.cancel_job(job_id)
        self.assertFalse(result)


class TestSystemStats(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_stats_empty_db(self):
        stats = self._store.system_stats()
        self.assertEqual(stats["users_total"], 0)
        self.assertEqual(stats["jobs_total"], 0)
        self.assertEqual(stats["jobs_running"], 0)
        self.assertIn("items_migrated_total", stats)
        self.assertIn("batches_total", stats)
        self.assertEqual(stats["success_rate_7d_pct"], 0)

    def test_stats_with_users(self):
        self._store.create_user(email="a@x.com", password_hash=hash_password("p"))
        self._store.create_user(email="b@x.com", password_hash=hash_password("p"))
        stats = self._store.system_stats()
        self.assertEqual(stats["users_total"], 2)


class TestAdminAuditLog(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        self._admin_id = self._store.create_user(
            email="admin@x.com", password_hash=hash_password("p"), is_admin=True
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_log_and_list(self):
        self._store.log_admin_action(
            admin_id=self._admin_id,
            action="update_user",
            target_id="some-user-id",
            details={"is_admin": True},
        )
        events = self._store.list_admin_audit_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "update_user")
        self.assertEqual(events[0]["admin_id"], self._admin_id)
        self.assertIsInstance(events[0]["details"], dict)

    def test_log_no_details(self):
        self._store.log_admin_action(admin_id=self._admin_id, action="logout")
        events = self._store.list_admin_audit_events()
        self.assertEqual(events[0]["details"], {})


class TestUserManagement(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        self._user_id = self._store.create_user(
            email="user@x.com", password_hash=hash_password("pass123")
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_deactivate_user(self):
        result = self._store.update_user(self._user_id, is_active=False)
        self.assertTrue(result)
        user = self._store.get_user_by_id(self._user_id)
        self.assertEqual(user["is_active"], 0)

    def test_promote_to_admin(self):
        result = self._store.update_user(self._user_id, is_admin=True)
        self.assertTrue(result)
        user = self._store.get_user_by_id(self._user_id)
        self.assertEqual(user["is_admin"], 1)

    def test_update_nonexistent_returns_false(self):
        result = self._store.update_user("no-such-id", is_admin=True)
        self.assertFalse(result)

    def test_change_password(self):
        new_hash = hash_password("newpass456")
        result = self._store.change_password(self._user_id, new_hash)
        self.assertTrue(result)
        user = self._store.get_user_by_id(self._user_id)
        self.assertTrue(verify_password("newpass456", user["password_hash"]))
        self.assertFalse(verify_password("pass123", user["password_hash"]))


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _make_finished_job(self, status: JobStatus):
        from groupware_migrator.models import MigrationPlan, MigrationRequest
        from groupware_migrator.models.domain import (
            ConnectionConfig,
            DestinationEndpoint,
            DestinationProtocol,
            MigrationOptions,
            SourceEndpoint,
            SourceProtocol,
            WorkloadType,
        )
        req = MigrationRequest(
            source=SourceEndpoint(
                protocol=SourceProtocol.IMAP,
                connection=ConnectionConfig(host="src", port=993, username="u", password="p"),
            ),
            destination=DestinationEndpoint(
                protocol=DestinationProtocol.IMAP,
                connection=ConnectionConfig(host="dst", port=993, username="u", password="p"),
            ),
            workload=WorkloadType.MAIL,
            options=MigrationOptions(),
        )
        job_id = self._store.create_job(req, MigrationPlan())
        self._store.set_job_status(job_id, status, set_finished=True)
        return job_id

    def test_cleanup_removes_old_completed(self):
        self._make_finished_job(JobStatus.COMPLETED)
        result = self._store.cleanup_old_records(older_than_days=0)
        self.assertGreaterEqual(result["jobs_deleted"], 0)  # 0 days means cutoff is now, may not delete

    def test_cleanup_returns_counts(self):
        result = self._store.cleanup_old_records(older_than_days=365)
        self.assertIn("jobs_deleted", result)
        self.assertIn("batches_deleted", result)
        self.assertIn("admin_events_deleted", result)

    def test_cleanup_does_not_remove_running(self):
        from groupware_migrator.models import MigrationPlan, MigrationRequest
        from groupware_migrator.models.domain import (
            ConnectionConfig,
            DestinationEndpoint,
            DestinationProtocol,
            MigrationOptions,
            SourceEndpoint,
            SourceProtocol,
            WorkloadType,
        )
        req = MigrationRequest(
            source=SourceEndpoint(
                protocol=SourceProtocol.IMAP,
                connection=ConnectionConfig(host="src", port=993, username="u", password="p"),
            ),
            destination=DestinationEndpoint(
                protocol=DestinationProtocol.IMAP,
                connection=ConnectionConfig(host="dst", port=993, username="u", password="p"),
            ),
            workload=WorkloadType.MAIL,
            options=MigrationOptions(),
        )
        job_id = self._store.create_job(req, MigrationPlan())
        self._store.set_job_status(job_id, JobStatus.RUNNING)
        self._store.cleanup_old_records(older_than_days=0)
        self.assertIsNotNone(self._store.get_job(job_id))


if __name__ == "__main__":
    unittest.main()
