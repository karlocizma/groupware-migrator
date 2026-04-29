import unittest
from pathlib import Path
import tempfile

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.engine.preflight import run_preflight
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import (
    DestinationProtocol,
    JobStatus,
    MailboxSnapshot,
    MigrationPlan,
    MigrationRequest,
    SourceProtocol,
)


class FakeSourceConnector(SourceConnector):
    protocol = SourceProtocol.IMAP

    def __init__(
        self,
        *,
        snapshots: list[MailboxSnapshot] | None = None,
        validate_error: Exception | None = None,
        list_error: Exception | None = None,
        pending_estimates: dict[tuple[str, str], int] | None = None,
    ):
        self._snapshots = snapshots or []
        self._validate_error = validate_error
        self._list_error = list_error
        self._pending_estimates = pending_estimates or {}

    def validate(self):
        if self._validate_error:
            raise self._validate_error
        return None

    def list_mailboxes(self):
        if self._list_error:
            raise self._list_error
        return list(self._snapshots)

    def iter_messages(self, mailbox: str, resume_from: str | None = None):
        return []

    def estimate_pending_messages(
        self,
        mailbox: str,
        resume_from: str | None,
    ) -> int | None:
        if resume_from is None:
            return None
        return self._pending_estimates.get((mailbox, resume_from))


class FakeDestinationConnector(DestinationConnector):
    protocol = DestinationProtocol.IMAP

    def __init__(self, *, validate_error: Exception | None = None):
        self._validate_error = validate_error

    def validate(self):
        if self._validate_error:
            raise self._validate_error
        return None

    def ensure_mailbox(self, mailbox: str):
        return None

    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ):
        return "1"


class TestRunPreflight(unittest.TestCase):
    def _request(self) -> MigrationRequest:
        return MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "imap",
                    "connection": {
                        "host": "imap.source",
                        "username": "source-user",
                        "password": "source-pass",
                    },
                },
                "destination": {
                    "protocol": "imap",
                    "connection": {
                        "host": "imap.destination",
                        "username": "dest-user",
                        "password": "dest-pass",
                    },
                },
            }
        )

    def test_preflight_success_with_planned_mailboxes(self):
        request = self._request()
        source = FakeSourceConnector(
            snapshots=[
                MailboxSnapshot(name="INBOX", estimated_messages=3),
                MailboxSnapshot(name="Archive", estimated_messages=2),
            ]
        )
        destination = FakeDestinationConnector()

        result = run_preflight(
            request,
            source_connector=source,
            destination_connector=destination,
        )

        self.assertTrue(result["source"]["ok"])
        self.assertTrue(result["destination"]["ok"])
        self.assertTrue(result["plan"]["ok"])
        self.assertEqual(result["plan"]["mailboxes"], 2)
        self.assertEqual(result["plan"]["total_estimated_messages"], 5)
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["overall_ok"])

    def test_preflight_source_validation_failure_skips_plan(self):
        request = self._request()
        source = FakeSourceConnector(validate_error=RuntimeError("source auth failed"))
        destination = FakeDestinationConnector()

        result = run_preflight(
            request,
            source_connector=source,
            destination_connector=destination,
        )

        self.assertFalse(result["source"]["ok"])
        self.assertIn("source auth failed", result["source"]["error"])
        self.assertTrue(result["destination"]["ok"])
        self.assertFalse(result["plan"]["ok"])
        self.assertEqual(
            result["plan"]["error"],
            "Skipped because source connection validation failed.",
        )
        self.assertFalse(result["overall_ok"])

    def test_preflight_destination_validation_failure(self):
        request = self._request()
        source = FakeSourceConnector(
            snapshots=[MailboxSnapshot(name="INBOX", estimated_messages=1)]
        )
        destination = FakeDestinationConnector(
            validate_error=RuntimeError("destination auth failed")
        )

        result = run_preflight(
            request,
            source_connector=source,
            destination_connector=destination,
        )

        self.assertTrue(result["source"]["ok"])
        self.assertFalse(result["destination"]["ok"])
        self.assertIn("destination auth failed", result["destination"]["error"])
        self.assertTrue(result["plan"]["ok"])
        self.assertEqual(result["plan"]["mailboxes"], 1)
        self.assertFalse(result["overall_ok"])

    def test_preflight_adds_warning_when_source_has_no_mailboxes(self):
        request = self._request()
        source = FakeSourceConnector(snapshots=[])
        destination = FakeDestinationConnector()

        result = run_preflight(
            request,
            source_connector=source,
            destination_connector=destination,
        )

        self.assertTrue(result["source"]["ok"])
        self.assertTrue(result["destination"]["ok"])
        self.assertTrue(result["plan"]["ok"])
        self.assertEqual(result["plan"]["mailboxes"], 0)
        self.assertIn("Source returned zero mailboxes.", result["warnings"])
        self.assertTrue(result["overall_ok"])

    def test_preflight_incremental_mode_requires_state_store(self):
        request = MigrationRequest.from_dict(
            {
                **self._request().to_dict(),
                "options": {
                    "sync_mode": "incremental",
                },
            }
        )
        source = FakeSourceConnector(
            snapshots=[MailboxSnapshot(name="INBOX", estimated_messages=2)]
        )
        destination = FakeDestinationConnector()

        result = run_preflight(
            request,
            source_connector=source,
            destination_connector=destination,
        )

        self.assertTrue(result["source"]["ok"])
        self.assertTrue(result["destination"]["ok"])
        self.assertFalse(result["plan"]["ok"])
        self.assertIn("state-backed cursor", result["incremental"]["error"])
        self.assertFalse(result["overall_ok"])

    def test_preflight_incremental_mode_resolves_base_job_cursors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            base_request = self._request()
            base_job_id = state_store.create_job(base_request, MigrationPlan())
            state_store.set_checkpoint(base_job_id, "INBOX", "20")
            state_store.set_job_status(
                base_job_id,
                JobStatus.COMPLETED,
                set_finished=True,
            )

            request = MigrationRequest.from_dict(
                {
                    **self._request().to_dict(),
                    "options": {
                        "sync_mode": "incremental",
                        "incremental_base_job_id": base_job_id,
                    },
                }
            )
            source = FakeSourceConnector(
                snapshots=[MailboxSnapshot(name="INBOX", estimated_messages=40)],
                pending_estimates={("INBOX", "20"): 4},
            )
            destination = FakeDestinationConnector()

            result = run_preflight(
                request,
                source_connector=source,
                destination_connector=destination,
                state_store=state_store,
            )

            self.assertTrue(result["plan"]["ok"])
            self.assertEqual(result["plan"]["total_estimated_messages"], 4)
            self.assertEqual(result["incremental"]["mode"], "incremental")
            self.assertEqual(result["incremental"]["resolution_source"], "base_job")
            self.assertEqual(result["incremental"]["resolved_cursor_mailboxes"], 1)
            self.assertEqual(result["incremental"]["base_job_id"], base_job_id)
            self.assertTrue(result["overall_ok"])


if __name__ == "__main__":
    unittest.main()
