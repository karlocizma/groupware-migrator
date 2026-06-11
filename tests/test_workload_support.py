import tempfile
import unittest
from pathlib import Path

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.connectors.dav import (
    CalDavDestinationConnector,
    CalDavSourceConnector,
    CardDavDestinationConnector,
    CardDavSourceConnector,
)
from groupware_migrator.connectors.factory import (
    create_destination_connector,
    create_source_connector,
)
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import (
    CollectionSnapshot,
    DestinationProtocol,
    MigrationPlanItem,
    MigrationRequest,
    SourceItem,
    SourceProtocol,
    WorkloadType,
)


class TestWorkloadSupport(unittest.TestCase):
    def test_calendar_request_infers_workload_and_aliases(self):
        request = MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "caldav",
                    "include_mailboxes": ["calendar/work"],
                    "connection": {
                        "host": "calendar.source.example.com/caldav/user/",
                        "username": "source-user",
                        "password": "source-pass",
                    },
                },
                "destination": {
                    "protocol": "caldav",
                    "root_mailbox": "Calendar-Migrated",
                    "connection": {
                        "host": "calendar.dest.example.com/caldav/user/",
                        "username": "dest-user",
                        "password": "dest-pass",
                    },
                },
                "collection_mapping": {
                    "calendar/work": "calendar/archived/work",
                },
            }
        )

        self.assertEqual(request.workload, WorkloadType.CALENDAR)
        self.assertEqual(request.source.include_collections, ["calendar/work"])
        self.assertEqual(request.destination.root_collection, "Calendar-Migrated")
        self.assertEqual(
            request.folder_mapping,
            {"calendar/work": "calendar/archived/work"},
        )

        serialized = request.to_dict()
        self.assertEqual(serialized["source"]["include_mailboxes"], ["calendar/work"])
        self.assertEqual(
            serialized["collection_mapping"],
            {"calendar/work": "calendar/archived/work"},
        )
        self.assertEqual(serialized["destination"]["root_mailbox"], "Calendar-Migrated")

    def test_contacts_request_infers_workload(self):
        request = MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "carddav",
                    "connection": {
                        "host": "contacts.source.example.com/addressbooks/user/",
                        "username": "source-user",
                        "password": "source-pass",
                    },
                },
                "destination": {
                    "protocol": "carddav",
                    "connection": {
                        "host": "contacts.dest.example.com/addressbooks/user/",
                        "username": "dest-user",
                        "password": "dest-pass",
                    },
                },
            }
        )
        self.assertEqual(request.workload, WorkloadType.CONTACTS)

    def test_dav_connector_factory_routing(self):
        calendar_request = MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "caldav",
                    "connection": {
                        "host": "calendar.source.example.com/caldav/user/",
                        "username": "source-user",
                        "password": "source-pass",
                    },
                },
                "destination": {
                    "protocol": "caldav",
                    "connection": {
                        "host": "calendar.dest.example.com/caldav/user/",
                        "username": "dest-user",
                        "password": "dest-pass",
                    },
                },
            }
        )
        self.assertIsInstance(
            create_source_connector(calendar_request),
            CalDavSourceConnector,
        )
        self.assertIsInstance(
            create_destination_connector(calendar_request),
            CalDavDestinationConnector,
        )

        contacts_request = MigrationRequest.from_dict(
            {
                "source": {
                    "protocol": "carddav",
                    "connection": {
                        "host": "contacts.source.example.com/addressbooks/user/",
                        "username": "source-user",
                        "password": "source-pass",
                    },
                },
                "destination": {
                    "protocol": "carddav",
                    "connection": {
                        "host": "contacts.dest.example.com/addressbooks/user/",
                        "username": "dest-user",
                        "password": "dest-pass",
                    },
                },
            }
        )
        self.assertIsInstance(
            create_source_connector(contacts_request),
            CardDavSourceConnector,
        )
        self.assertIsInstance(
            create_destination_connector(contacts_request),
            CardDavDestinationConnector,
        )

    def test_migration_plan_item_accepts_legacy_mail_keywords(self):
        item = MigrationPlanItem(
            source_mailbox="INBOX",
            destination_mailbox="Migrated/INBOX",
            estimated_messages=15,
        )

        self.assertEqual(item.source_collection, "INBOX")
        self.assertEqual(item.destination_collection, "Migrated/INBOX")
        self.assertEqual(item.estimated_items, 15)


def _caldav_request_dict(workload: str) -> dict:
    return {
        "workload": workload,
        "source": {
            "protocol": "caldav",
            "connection": {
                "host": "dav.source.example.com/dav/user/",
                "username": "src-user",
                "password": "src-pass",
            },
        },
        "destination": {
            "protocol": "caldav",
            "connection": {
                "host": "dav.dest.example.com/dav/user/",
                "username": "dst-user",
                "password": "dst-pass",
            },
        },
    }


class FakeDavSourceConnector(SourceConnector):
    protocol = SourceProtocol.CALDAV

    def __init__(self, collections: dict[str, list[SourceItem]]):
        self._collections = collections

    def validate(self) -> None:
        return None

    def list_mailboxes(self):
        return []

    def list_collections(self) -> list[CollectionSnapshot]:
        return [
            CollectionSnapshot(name=name, estimated_items=len(items))
            for name, items in self._collections.items()
        ]

    def iter_messages(self, mailbox, resume_from=None):
        return iter([])

    def iter_items(self, collection, resume_from=None):
        for item in self._collections.get(collection, []):
            if resume_from and item.source_id <= resume_from:
                continue
            yield item


class FakeDavDestinationConnector(DestinationConnector):
    protocol = DestinationProtocol.CALDAV

    def __init__(self):
        self.collections: set[str] = set()
        self.items: list[tuple[str, str, bytes]] = []

    def validate(self) -> None:
        return None

    def ensure_mailbox(self, mailbox: str) -> None:
        self.collections.add(mailbox)

    def ensure_collection(self, collection: str) -> None:
        self.collections.add(collection)

    def append_message(self, mailbox, raw_message, *, flags=None, internal_date=None):
        raise NotImplementedError

    def upsert_item(self, collection, source_id, raw_payload, *, metadata=None) -> str | None:
        self.items.append((collection, source_id, raw_payload))
        return f"dest:{source_id}"


def _ics_vtodo(uid: str) -> bytes:
    return (
        f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        f"BEGIN:VTODO\r\nUID:{uid}\r\nSUMMARY:Task {uid}\r\nEND:VTODO\r\n"
        f"END:VCALENDAR\r\n"
    ).encode()


def _ics_vjournal(uid: str) -> bytes:
    return (
        f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        f"BEGIN:VJOURNAL\r\nUID:{uid}\r\nSUMMARY:Note {uid}\r\nEND:VJOURNAL\r\n"
        f"END:VCALENDAR\r\n"
    ).encode()


class TestTasksNotesWorkloads(unittest.TestCase):

    def test_tasks_workload_parses(self):
        request = MigrationRequest.from_dict(_caldav_request_dict("tasks"))
        self.assertEqual(request.workload, WorkloadType.TASKS)

    def test_notes_workload_parses(self):
        request = MigrationRequest.from_dict(_caldav_request_dict("notes"))
        self.assertEqual(request.workload, WorkloadType.NOTES)

    def test_tasks_workload_round_trips_to_dict(self):
        request = MigrationRequest.from_dict(_caldav_request_dict("tasks"))
        self.assertEqual(request.to_dict()["workload"], "tasks")

    def test_notes_workload_round_trips_to_dict(self):
        request = MigrationRequest.from_dict(_caldav_request_dict("notes"))
        self.assertEqual(request.to_dict()["workload"], "notes")

    def test_tasks_workload_rejects_imap_source(self):
        with self.assertRaises(ValueError):
            MigrationRequest.from_dict({
                "workload": "tasks",
                "source": {
                    "protocol": "imap",
                    "connection": {"host": "imap.example.com", "username": "u", "password": "p"},
                },
                "destination": {
                    "protocol": "caldav",
                    "connection": {"host": "dav.example.com", "username": "u", "password": "p"},
                },
            })

    def test_notes_workload_rejects_imap_source(self):
        with self.assertRaises(ValueError):
            MigrationRequest.from_dict({
                "workload": "notes",
                "source": {
                    "protocol": "imap",
                    "connection": {"host": "imap.example.com", "username": "u", "password": "p"},
                },
                "destination": {
                    "protocol": "caldav",
                    "connection": {"host": "dav.example.com", "username": "u", "password": "p"},
                },
            })

    def test_tasks_workload_rejects_imap_destination(self):
        with self.assertRaises(ValueError):
            MigrationRequest.from_dict({
                "workload": "tasks",
                "source": {
                    "protocol": "caldav",
                    "connection": {"host": "dav.example.com", "username": "u", "password": "p"},
                },
                "destination": {
                    "protocol": "imap",
                    "connection": {"host": "imap.example.com", "username": "u", "password": "p"},
                },
            })

    def test_tasks_factory_routes_to_caldav(self):
        request = MigrationRequest.from_dict(_caldav_request_dict("tasks"))
        self.assertIsInstance(create_source_connector(request), CalDavSourceConnector)
        self.assertIsInstance(create_destination_connector(request), CalDavDestinationConnector)

    def test_notes_factory_routes_to_caldav(self):
        request = MigrationRequest.from_dict(_caldav_request_dict("notes"))
        self.assertIsInstance(create_source_connector(request), CalDavSourceConnector)
        self.assertIsInstance(create_destination_connector(request), CalDavDestinationConnector)

    def _run_workload(self, workload: str, items: dict[str, list[SourceItem]]):
        request = MigrationRequest.from_dict(_caldav_request_dict(workload))
        src = FakeDavSourceConnector(items)
        dst = FakeDavDestinationConnector()
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStateStore(Path(tmp) / "state.db")
            runner = MigrationRunner(store)
            report = runner.run(request, source_connector=src, destination_connector=dst)
        return report, dst

    def test_runner_migrates_tasks_items(self):
        items = {
            "task-lists/work": [
                SourceItem(
                    source_collection="task-lists/work",
                    source_id="todo-1.ics",
                    raw_payload=_ics_vtodo("todo-1"),
                    content_type="text/calendar",
                    version_token="etag-1",
                    item_key="todo-1.ics",
                    metadata={},
                ),
                SourceItem(
                    source_collection="task-lists/work",
                    source_id="todo-2.ics",
                    raw_payload=_ics_vtodo("todo-2"),
                    content_type="text/calendar",
                    version_token="etag-2",
                    item_key="todo-2.ics",
                    metadata={},
                ),
            ],
        }
        report, dst = self._run_workload("tasks", items)
        self.assertEqual(report.migrated_count, 2)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(len(dst.items), 2)
        dest_ids = [item[1] for item in dst.items]
        self.assertIn("todo-1.ics", dest_ids)
        self.assertIn("todo-2.ics", dest_ids)

    def test_runner_migrates_notes_items(self):
        items = {
            "notes/personal": [
                SourceItem(
                    source_collection="notes/personal",
                    source_id="note-a.ics",
                    raw_payload=_ics_vjournal("note-a"),
                    content_type="text/calendar",
                    version_token="etag-a",
                    item_key="note-a.ics",
                    metadata={},
                ),
            ],
        }
        report, dst = self._run_workload("notes", items)
        self.assertEqual(report.migrated_count, 1)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(dst.items[0][2], _ics_vjournal("note-a"))

    def test_runner_skips_duplicate_tasks_within_same_job(self):
        # Same item appearing twice in the iterator (fingerprint dedup is per-job)
        dup_item = SourceItem(
            source_collection="task-lists/work",
            source_id="todo-dup.ics",
            raw_payload=_ics_vtodo("todo-dup"),
            content_type="text/calendar",
            version_token="etag-dup",
            item_key="todo-dup.ics",
            metadata={},
        )
        items = {"task-lists/work": [dup_item, dup_item]}
        report, dst = self._run_workload("tasks", items)
        self.assertEqual(report.migrated_count + report.skipped_count, 2)
        self.assertEqual(report.migrated_count, 1)
        self.assertEqual(report.skipped_count, 1)
        self.assertEqual(len(dst.items), 1)


if __name__ == "__main__":
    unittest.main()
