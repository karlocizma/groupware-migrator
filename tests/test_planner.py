import unittest

from groupware_migrator.connectors.base import SourceConnector
from groupware_migrator.engine.planner import MigrationPlanner
from groupware_migrator.models import (
    MailboxSnapshot,
    MigrationRequest,
    SourceProtocol,
)


class FakeSourceConnector(SourceConnector):
    protocol = SourceProtocol.IMAP

    def __init__(self, snapshots, *, pending_estimates=None):
        self._snapshots = snapshots
        self._pending_estimates = pending_estimates or {}
        self.estimate_calls: list[tuple[str, str | None]] = []

    def validate(self):
        return None

    def list_mailboxes(self):
        return list(self._snapshots)

    def iter_messages(self, mailbox: str, resume_from: str | None = None):
        return []

    def estimate_pending_messages(
        self,
        mailbox: str,
        resume_from: str | None,
    ) -> int | None:
        self.estimate_calls.append((mailbox, resume_from))
        if resume_from is None:
            return None
        return self._pending_estimates.get((mailbox, resume_from))


class TestMigrationPlanner(unittest.TestCase):
    def test_planner_applies_folder_mapping_and_root(self):
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
                "folder_mapping": {"Archive": "Historical/Archive"},
            }
        )
        source_connector = FakeSourceConnector(
            [
                MailboxSnapshot(name="INBOX", estimated_messages=2),
                MailboxSnapshot(name="Archive", estimated_messages=3),
            ]
        )
        planner = MigrationPlanner()
        plan = planner.build_plan(request, source_connector)

        destinations = {item.source_mailbox: item.destination_mailbox for item in plan.items}
        self.assertEqual(destinations["INBOX"], "Migrated/INBOX")
        self.assertEqual(destinations["Archive"], "Migrated/Historical/Archive")
        self.assertEqual(plan.total_estimated_messages, 5)

    def test_planner_filters_requested_mailboxes(self):
        request = MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "imap",
                    "connection": {
                        "host": "imap.source",
                        "username": "user",
                        "password": "pass",
                    },
                    "include_mailboxes": ["INBOX"],
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
            }
        )
        source_connector = FakeSourceConnector(
            [
                MailboxSnapshot(name="INBOX", estimated_messages=1),
                MailboxSnapshot(name="Archive", estimated_messages=1),
            ]
        )
        planner = MigrationPlanner()
        plan = planner.build_plan(request, source_connector)
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].source_mailbox, "INBOX")

    def test_planner_pop3_destination_mailbox(self):
        request = MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "pop3",
                    "connection": {
                        "host": "pop.source",
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
                    "pop3_destination_mailbox": "Imported/POP3",
                },
            }
        )
        source_connector = FakeSourceConnector(
            [MailboxSnapshot(name="INBOX", estimated_messages=4)]
        )
        planner = MigrationPlanner()
        plan = planner.build_plan(request, source_connector)
        self.assertEqual(plan.items[0].destination_mailbox, "Migrated/Imported/POP3")

    def test_planner_incremental_uses_pending_estimate(self):
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
                },
            }
        )
        source_connector = FakeSourceConnector(
            [MailboxSnapshot(name="INBOX", estimated_messages=100)],
            pending_estimates={("INBOX", "55"): 7},
        )
        planner = MigrationPlanner()
        plan = planner.build_plan(
            request,
            source_connector,
            incremental_cursors={"INBOX": "55"},
        )
        self.assertEqual(plan.items[0].estimated_messages, 7)
        self.assertEqual(source_connector.estimate_calls, [("INBOX", "55")])


if __name__ == "__main__":
    unittest.main()