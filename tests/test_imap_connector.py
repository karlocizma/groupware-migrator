import unittest

from groupware_migrator.connectors.imap import (
    ImapDestinationConnector,
    _parse_list_entry,
)
from groupware_migrator.models import ConnectionConfig


class TestImapConnectorHelpers(unittest.TestCase):
    def test_parse_list_entry_basic(self):
        parsed = _parse_list_entry(b'(\\HasNoChildren) "/" "INBOX"')
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.mailbox, "INBOX")
        self.assertEqual(parsed.delimiter, "/")
        self.assertIn("\\hasnochildren", parsed.flags)

    def test_parse_list_entry_noselect(self):
        parsed = _parse_list_entry(b'(\\Noselect \\HasChildren) "." "[Gmail]"')
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.mailbox, "[Gmail]")
        self.assertEqual(parsed.delimiter, ".")
        self.assertIn("\\noselect", parsed.flags)

    def test_destination_mailbox_parts_follow_delimiter(self):
        connector = ImapDestinationConnector(
            ConnectionConfig(
                host="imap.example.com",
                port=993,
                username="user",
                password="pass",
            )
        )
        parts = connector._mailbox_path_parts("Migrated/Sub/Child", delimiter=".")
        self.assertEqual(parts, ["Migrated", "Migrated.Sub", "Migrated.Sub.Child"])
        formatted = connector._format_mailbox("Migrated/Sub/Child", delimiter=".")
        self.assertEqual(formatted, "Migrated.Sub.Child")


if __name__ == "__main__":
    unittest.main()
