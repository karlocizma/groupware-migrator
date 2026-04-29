import unittest

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
from groupware_migrator.models import MigrationPlanItem, MigrationRequest, WorkloadType


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


if __name__ == "__main__":
    unittest.main()
