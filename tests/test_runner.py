from pathlib import Path
import tempfile
import unittest

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import (
    DestinationProtocol,
    JobStatus,
    MailboxSnapshot,
    MigrationRequest,
    SourceMessage,
    SourceProtocol,
)


def _raw_message(message_id: str, subject: str) -> bytes:
    return (
        f"Message-ID: <{message_id}>\r\n"
        f"From: sender@example.com\r\n"
        f"To: receiver@example.com\r\n"
        f"Subject: {subject}\r\n"
        "\r\n"
        "Body"
    ).encode("utf-8")


class FakeSourceConnector(SourceConnector):
    protocol = SourceProtocol.IMAP

    def __init__(self, mailbox_snapshots, mailbox_messages):
        self._mailbox_snapshots = mailbox_snapshots
        self._mailbox_messages = mailbox_messages

    def validate(self):
        return None

    def list_mailboxes(self):
        return list(self._mailbox_snapshots)

    def iter_messages(self, mailbox: str, resume_from: str | None = None):
        messages = list(self._mailbox_messages.get(mailbox, []))
        if resume_from:
            source_ids = [message.source_id for message in messages]
            if resume_from in source_ids:
                messages = messages[source_ids.index(resume_from) + 1 :]
        return messages


class FakeDestinationConnector(DestinationConnector):
    protocol = DestinationProtocol.IMAP

    def __init__(self, *, fail_on_source_id: str | None = None):
        self._mailboxes = set()
        self.messages = []
        self.fail_on_source_id = fail_on_source_id

    def validate(self):
        return None

    def ensure_mailbox(self, mailbox: str):
        self._mailboxes.add(mailbox)

    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ):
        marker = f"source-id:{self.fail_on_source_id}".encode("utf-8")
        if self.fail_on_source_id and marker in raw_message:
            raise RuntimeError("Simulated append failure.")
        self.messages.append((mailbox, raw_message, flags or set(), internal_date))
        return str(len(self.messages))


class TestMigrationRunner(unittest.TestCase):
    def _request(self, *, max_errors: int = 5):
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
                    "root_mailbox": "Migrated",
                },
                "options": {
                    "max_errors": max_errors,
                },
            }
        )

    def test_runner_skips_duplicate_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            runner = MigrationRunner(state_store)

            raw = _raw_message("same@example.com", "Subject")
            source_connector = FakeSourceConnector(
                [MailboxSnapshot(name="INBOX", estimated_messages=2)],
                {
                    "INBOX": [
                        SourceMessage(
                            source_mailbox="INBOX",
                            source_id="1",
                            raw_message=raw,
                            message_id="same@example.com",
                        ),
                        SourceMessage(
                            source_mailbox="INBOX",
                            source_id="2",
                            raw_message=raw,
                            message_id="same@example.com",
                        ),
                    ]
                },
            )
            destination_connector = FakeDestinationConnector()

            report = runner.run(
                request=self._request(),
                source_connector=source_connector,
                destination_connector=destination_connector,
            )

            self.assertEqual(report.status, JobStatus.COMPLETED)
            self.assertEqual(report.migrated_count, 1)
            self.assertEqual(report.skipped_count, 1)
            self.assertEqual(report.failed_count, 0)
            self.assertEqual(len(destination_connector.messages), 1)

    def test_runner_resumes_failed_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            runner = MigrationRunner(state_store)

            msg_one = SourceMessage(
                source_mailbox="INBOX",
                source_id="1",
                raw_message=_raw_message("one@example.com", "First message"),
                message_id="one@example.com",
            )
            msg_two = SourceMessage(
                source_mailbox="INBOX",
                source_id="2",
                raw_message=_raw_message("two@example.com", "source-id:2"),
                message_id="two@example.com",
            )
            source_connector = FakeSourceConnector(
                [MailboxSnapshot(name="INBOX", estimated_messages=2)],
                {"INBOX": [msg_one, msg_two]},
            )

            failing_destination = FakeDestinationConnector(fail_on_source_id="2")
            first_report = runner.run(
                request=self._request(max_errors=1),
                source_connector=source_connector,
                destination_connector=failing_destination,
            )

            self.assertEqual(first_report.status, JobStatus.FAILED)
            self.assertEqual(first_report.migrated_count, 1)
            self.assertEqual(first_report.failed_count, 1)

            healthy_destination = FakeDestinationConnector()
            second_report = runner.run(
                request=self._request(max_errors=1),
                source_connector=source_connector,
                destination_connector=healthy_destination,
                resume_job_id=first_report.job_id,
            )

            self.assertEqual(second_report.status, JobStatus.COMPLETED)
            self.assertEqual(second_report.migrated_count, 2)
            self.assertEqual(second_report.failed_count, 1)

    def test_runner_incremental_resumes_from_sync_cursor_and_updates_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            runner = MigrationRunner(state_store)
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
                        "root_mailbox": "Migrated",
                    },
                    "options": {
                        "sync_mode": "incremental",
                        "max_errors": 5,
                    },
                }
            )

            sync_key = state_store.build_sync_key(request)
            state_store.set_sync_cursor(
                sync_key=sync_key,
                source_mailbox="INBOX",
                source_id="1",
            )

            source_connector = FakeSourceConnector(
                [MailboxSnapshot(name="INBOX", estimated_messages=3)],
                {
                    "INBOX": [
                        SourceMessage(
                            source_mailbox="INBOX",
                            source_id="1",
                            raw_message=_raw_message("one@example.com", "First"),
                            message_id="one@example.com",
                        ),
                        SourceMessage(
                            source_mailbox="INBOX",
                            source_id="2",
                            raw_message=_raw_message("two@example.com", "Second"),
                            message_id="two@example.com",
                        ),
                        SourceMessage(
                            source_mailbox="INBOX",
                            source_id="3",
                            raw_message=_raw_message("three@example.com", "Third"),
                            message_id="three@example.com",
                        ),
                    ]
                },
            )
            destination_connector = FakeDestinationConnector()

            report = runner.run(
                request=request,
                source_connector=source_connector,
                destination_connector=destination_connector,
            )

            self.assertEqual(report.status, JobStatus.COMPLETED)
            self.assertEqual(report.migrated_count, 2)
            self.assertEqual(report.failed_count, 0)
            self.assertEqual(len(destination_connector.messages), 2)
            self.assertEqual(
                state_store.get_sync_cursor(sync_key=sync_key, source_mailbox="INBOX"),
                "3",
            )


if __name__ == "__main__":
    unittest.main()
