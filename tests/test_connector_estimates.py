from contextlib import contextmanager
import unittest

from groupware_migrator.connectors.imap import ImapSourceConnector
from groupware_migrator.connectors.pop3 import Pop3SourceConnector
from groupware_migrator.models import ConnectionConfig


class _FakeImapClient:
    def __init__(self, search_payload: bytes):
        self._search_payload = search_payload
        self.closed = False

    def select(self, _mailbox: str, readonly: bool = True):
        _ = readonly
        return "OK", [b""]

    def uid(self, command, *_args):
        if str(command).upper() == "SEARCH":
            return "OK", [self._search_payload]
        return "NO", []

    def close(self):
        self.closed = True


class _FakePop3Client:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def list(self):
        return b"+OK", self._lines, 0


class TestConnectorPendingEstimates(unittest.TestCase):
    def test_imap_estimate_pending_messages(self):
        connector = ImapSourceConnector(
            ConnectionConfig(
                host="imap.example.com",
                port=993,
                username="user",
                password="pass",
            )
        )
        fake_client = _FakeImapClient(b"1 2 3")

        @contextmanager
        def fake_connect():
            yield fake_client

        connector._connect = fake_connect  # type: ignore[method-assign]
        estimate = connector.estimate_pending_messages("INBOX", "2")
        self.assertEqual(estimate, 1)
        self.assertTrue(fake_client.closed)

    def test_pop3_estimate_pending_messages(self):
        connector = Pop3SourceConnector(
            ConnectionConfig(
                host="pop.example.com",
                port=995,
                username="user",
                password="pass",
            )
        )
        fake_client = _FakePop3Client([b"1 123", b"2 456", b"3 789"])

        @contextmanager
        def fake_connect():
            yield fake_client

        connector._connect = fake_connect  # type: ignore[method-assign]
        connector._build_uid_lookup = lambda _client: {  # type: ignore[method-assign]
            1: "uid-1",
            2: "uid-2",
            3: "uid-3",
        }
        estimate = connector.estimate_pending_messages("INBOX", "uid-2")
        self.assertEqual(estimate, 1)


if __name__ == "__main__":
    unittest.main()
