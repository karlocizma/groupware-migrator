from pathlib import Path
import tempfile
import time
import unittest

from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import (
    JobStatus,
    MigrationPlan,
    MigrationPlanItem,
    MigrationRequest,
)


class TestSQLiteStateStore(unittest.TestCase):
    def test_create_job_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)

            request = MigrationRequest.from_dict(
                {
                    "job_name": "test-job",
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )
            plan = MigrationPlan(
                items=[
                    MigrationPlanItem(
                        source_mailbox="INBOX",
                        destination_mailbox="Migrated/INBOX",
                        estimated_messages=10,
                    )
                ]
            )

            job_id = state_store.create_job(request, plan)
            self.assertTrue(state_store.has_job(job_id))

            state_store.set_checkpoint(job_id, "INBOX", "123")
            self.assertEqual(state_store.get_checkpoint(job_id, "INBOX"), "123")

    def test_record_message_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)

            request = MigrationRequest.from_dict(
                {
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )
            plan = MigrationPlan()
            job_id = state_store.create_job(request, plan)

            inserted_first = state_store.record_message(
                job_id,
                fingerprint="abc",
                source_mailbox="INBOX",
                source_id="1",
                destination_mailbox="Migrated/INBOX",
                destination_id="10",
            )
            inserted_second = state_store.record_message(
                job_id,
                fingerprint="abc",
                source_mailbox="INBOX",
                source_id="1",
                destination_mailbox="Migrated/INBOX",
                destination_id="10",
            )
            self.assertTrue(inserted_first)
            self.assertFalse(inserted_second)
            self.assertTrue(state_store.has_fingerprint(job_id, "abc"))

    def test_list_jobs_respects_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)

            request = MigrationRequest.from_dict(
                {
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )
            plan = MigrationPlan()

            first_job = state_store.create_job(request, plan)
            time.sleep(0.001)
            second_job = state_store.create_job(request, plan)
            time.sleep(0.001)
            third_job = state_store.create_job(request, plan)

            jobs = state_store.list_jobs(limit=2)
            self.assertEqual(len(jobs), 2)
            ids = [job["job_id"] for job in jobs]
            self.assertIn(third_job, ids)
            self.assertIn(second_job, ids)
            self.assertNotIn(first_job, ids)

    def test_append_and_list_audit_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)

            request = MigrationRequest.from_dict(
                {
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )
            job_id = state_store.create_job(request, MigrationPlan())

            state_store.append_audit_event(
                job_id,
                "job_started",
                payload={"dry_run": False},
            )
            state_store.append_audit_event(
                job_id,
                "mailbox_completed",
                event_level="info",
                payload={"source_mailbox": "INBOX"},
            )

            events = state_store.list_audit_events(job_id, limit=10)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event_type"], "mailbox_completed")
            self.assertEqual(events[1]["event_type"], "job_started")
            self.assertEqual(events[1]["payload"]["dry_run"], False)

    def test_batch_summary_and_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)

            request = MigrationRequest.from_dict(
                {
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )
            job_one = state_store.create_job(request, MigrationPlan())
            job_two = state_store.create_job(request, MigrationPlan())

            state_store.set_job_status(job_one, JobStatus.RUNNING, set_started=True)
            state_store.increment_counters(job_one, migrated=5, skipped=1)
            state_store.set_job_status(job_one, JobStatus.COMPLETED, set_finished=True)

            state_store.set_job_status(
                job_two,
                JobStatus.FAILED,
                set_finished=True,
                last_error="test failure",
            )
            state_store.increment_counters(job_two, failed=2)

            batch_id = state_store.create_batch(batch_name="wave-1", total_rows=3)
            state_store.add_batch_item(
                batch_id=batch_id,
                row_number=2,
                source_username="user1@source.example.com",
                destination_username="user1@dest.example.com",
                job_id=job_one,
                job_name="user-1",
            )
            state_store.add_batch_item(
                batch_id=batch_id,
                row_number=3,
                source_username="user2@source.example.com",
                destination_username="user2@dest.example.com",
                job_id=job_two,
                job_name="user-2",
            )
            state_store.add_batch_item(
                batch_id=batch_id,
                row_number=4,
                source_username="broken@source.example.com",
                destination_username="broken@dest.example.com",
                submit_error="validation failed",
                job_name="broken-row",
            )

            batch_row = state_store.get_batch(batch_id)
            assert batch_row is not None
            self.assertEqual(batch_row["batch_name"], "wave-1")
            self.assertEqual(batch_row["total_rows"], 3)
            self.assertEqual(batch_row["completed_rows"], 1)
            self.assertEqual(batch_row["failed_rows"], 2)
            self.assertEqual(batch_row["status"], JobStatus.FAILED.value)
            self.assertEqual(batch_row["migrated_count"], 5)
            self.assertEqual(batch_row["skipped_count"], 1)
            self.assertEqual(batch_row["message_failed_count"], 2)

            items = state_store.list_batch_items(batch_id)
            self.assertEqual(len(items), 3)
            self.assertEqual(items[0]["status"], JobStatus.COMPLETED.value)
            self.assertEqual(items[1]["status"], JobStatus.FAILED.value)
            self.assertEqual(items[2]["status"], JobStatus.FAILED.value)
            self.assertEqual(items[2]["last_error"], "validation failed")

    def test_resolve_incremental_cursors_requires_completed_base_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)
            request = MigrationRequest.from_dict(
                {
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )

            base_job_id = state_store.create_job(request, MigrationPlan())
            state_store.set_checkpoint(base_job_id, "INBOX", "42")
            with self.assertRaises(ValueError):
                state_store.resolve_incremental_cursors(
                    request,
                    base_job_id=base_job_id,
                )

            state_store.set_job_status(
                base_job_id,
                JobStatus.COMPLETED,
                set_finished=True,
            )
            cursors = state_store.resolve_incremental_cursors(
                request,
                base_job_id=base_job_id,
            )
            self.assertEqual(cursors, {"INBOX": "42"})

    def test_update_sync_cursors_from_job_uses_job_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            state_store = SQLiteStateStore(db_path)
            request = MigrationRequest.from_dict(
                {
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )

            job_id = state_store.create_job(request, MigrationPlan())
            state_store.set_checkpoint(job_id, "INBOX", "100")
            state_store.set_checkpoint(job_id, "Archive", "55")
            state_store.update_sync_cursors_from_job(request, job_id=job_id)

            sync_key = state_store.build_sync_key(request)
            self.assertEqual(
                state_store.list_sync_cursors(sync_key=sync_key),
                {"Archive": "55", "INBOX": "100"},
            )


class TestRecoverStuckJobs(unittest.TestCase):
    def _make_store(self, temp_dir: str) -> SQLiteStateStore:
        return SQLiteStateStore(Path(temp_dir) / "state.db")

    def _make_request(self) -> MigrationRequest:
        return MigrationRequest.from_dict({
            "source": {
                "protocol": "imap",
                "connection": {"host": "src", "username": "u", "password": "p"},
            },
            "destination": {
                "protocol": "imap",
                "connection": {"host": "dst", "username": "u", "password": "p"},
            },
        })

    def test_recover_stuck_jobs_marks_running_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            request = self._make_request()
            plan = MigrationPlan()

            # Create a job and manually force it into running state
            job_id = store.create_job(request, plan)
            store.set_job_status(job_id, JobStatus.RUNNING, set_started=True)

            recovered = store.recover_stuck_jobs()

            self.assertEqual(recovered, 1)
            job = store.get_job(job_id)
            self.assertEqual(job["status"], JobStatus.FAILED.value)
            self.assertIsNotNone(job["last_error"])
            self.assertIsNotNone(job["finished_at"])

    def test_recover_stuck_jobs_ignores_completed_and_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            request = self._make_request()

            job_pending = store.create_job(request, MigrationPlan())
            job_completed = store.create_job(request, MigrationPlan())
            store.set_job_status(job_completed, JobStatus.COMPLETED, set_finished=True)

            recovered = store.recover_stuck_jobs()

            self.assertEqual(recovered, 0)
            self.assertEqual(store.get_job(job_pending)["status"], JobStatus.PENDING.value)
            self.assertEqual(store.get_job(job_completed)["status"], JobStatus.COMPLETED.value)


if __name__ == "__main__":
    unittest.main()
