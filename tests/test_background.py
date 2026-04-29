from pathlib import Path
import tempfile
import time
import unittest

from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import JobStatus, MigrationRequest


class FakeRunner:
    def __init__(self, state_store: SQLiteStateStore):
        self.state_store = state_store
        self.run_calls: list[str] = []

    def run(self, *, request: MigrationRequest, resume_job_id: str | None = None):
        if not resume_job_id:
            raise ValueError("resume_job_id must be provided.")
        self.run_calls.append(resume_job_id)
        self.state_store.set_job_status(
            resume_job_id,
            JobStatus.RUNNING,
            set_started=True,
        )
        time.sleep(0.02)
        self.state_store.increment_counters(resume_job_id, migrated=1)
        self.state_store.set_job_status(
            resume_job_id,
            JobStatus.COMPLETED,
            set_finished=True,
        )
        return {"job_id": resume_job_id}


class TestBackgroundJobManager(unittest.TestCase):
    def _request(self) -> MigrationRequest:
        return MigrationRequest.from_dict(
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

    def test_start_job_runs_in_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            fake_runner = FakeRunner(state_store)
            manager = BackgroundJobManager(
                state_store=state_store,
                runner=fake_runner,  # type: ignore[arg-type]
                max_workers=1,
            )

            try:
                job_id = manager.start_job(self._request())
                self.assertTrue(state_store.has_job(job_id))

                completed = False
                for _ in range(100):
                    row = state_store.get_job(job_id)
                    if row and row["status"] == JobStatus.COMPLETED.value:
                        completed = True
                        break
                    time.sleep(0.01)

                self.assertTrue(completed)
                self.assertFalse(manager.is_running(job_id))
                row = state_store.get_job(job_id)
                self.assertIsNotNone(row)
                self.assertEqual(row["migrated_count"], 1)
                self.assertEqual(fake_runner.run_calls, [job_id])
            finally:
                manager.shutdown(wait=True)

    def test_resume_requires_existing_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            fake_runner = FakeRunner(state_store)
            manager = BackgroundJobManager(
                state_store=state_store,
                runner=fake_runner,  # type: ignore[arg-type]
                max_workers=1,
            )

            try:
                with self.assertRaises(ValueError):
                    manager.resume_job(
                        request=self._request(),
                        job_id="missing-job",
                    )
            finally:
                manager.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
