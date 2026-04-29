import base64
import unittest
from unittest.mock import patch

from groupware_migrator.connectors.imap import ImapSourceConnector
from groupware_migrator.connectors.pop3 import Pop3SourceConnector
from groupware_migrator.models import AuthMode, ConnectionConfig


class _FakeImapClient:
    def __init__(self):
        self.login_calls: list[tuple[str, str]] = []
        self.authenticate_calls: list[tuple[str, bytes]] = []

    def login(self, username: str, password: str):
        self.login_calls.append((username, password))

    def authenticate(self, mechanism: str, callback):
        payload = callback(b"")
        self.authenticate_calls.append((mechanism, payload))
        return "OK", [b"authenticated"]


class _FakePop3Client:
    def __init__(self):
        self.user_calls: list[str] = []
        self.pass_calls: list[str] = []
        self.commands: list[str] = []
        self.lines: list[bytes] = []
        self.responses: list[bytes] = [b"+ ", b"+OK authenticated"]

    def user(self, username: str):
        self.user_calls.append(username)

    def pass_(self, password: str):
        self.pass_calls.append(password)

    def _putcmd(self, command: str):
        self.commands.append(command)

    def _getresp(self):
        if not self.responses:
            return b"+OK"
        return self.responses.pop(0)

    def _putline(self, line: bytes):
        self.lines.append(line)


class TestImapConnectorAuth(unittest.TestCase):
    def test_password_auth_uses_login(self):
        connector = ImapSourceConnector(
            ConnectionConfig(
                host="imap.example.com",
                port=993,
                username="user@example.com",
                password="password-123",
                auth_mode=AuthMode.PASSWORD,
            )
        )
        client = _FakeImapClient()
        connector._authenticate(client)
        self.assertEqual(client.login_calls, [("user@example.com", "password-123")])
        self.assertEqual(client.authenticate_calls, [])

    def test_oauth_auth_uses_xoauth2(self):
        connector = ImapSourceConnector(
            ConnectionConfig(
                host="imap.example.com",
                port=993,
                username="user@example.com",
                auth_mode=AuthMode.OAUTH2,
            )
        )
        client = _FakeImapClient()
        with patch(
            "groupware_migrator.connectors.imap.resolve_oauth_access_token",
            return_value="oauth-access-token",
        ):
            connector._authenticate(client)

        self.assertEqual(client.login_calls, [])
        self.assertEqual(len(client.authenticate_calls), 1)
        mechanism, raw_payload = client.authenticate_calls[0]
        self.assertEqual(mechanism, "XOAUTH2")
        self.assertIn(b"user=user@example.com", raw_payload)
        self.assertIn(b"auth=Bearer oauth-access-token", raw_payload)


class TestPop3ConnectorAuth(unittest.TestCase):
    def test_password_auth_uses_user_pass(self):
        connector = Pop3SourceConnector(
            ConnectionConfig(
                host="pop.example.com",
                port=995,
                username="user@example.com",
                password="password-123",
                auth_mode=AuthMode.PASSWORD,
            )
        )
        client = _FakePop3Client()
        connector._authenticate(client)
        self.assertEqual(client.user_calls, ["user@example.com"])
        self.assertEqual(client.pass_calls, ["password-123"])
        self.assertEqual(client.commands, [])

    def test_oauth_auth_uses_xoauth2(self):
        connector = Pop3SourceConnector(
            ConnectionConfig(
                host="pop.example.com",
                port=995,
                username="user@example.com",
                auth_mode=AuthMode.OAUTH2,
            )
        )
        client = _FakePop3Client()
        with patch(
            "groupware_migrator.connectors.pop3.resolve_oauth_access_token",
            return_value="oauth-access-token",
        ):
            connector._authenticate(client)

        self.assertEqual(client.user_calls, [])
        self.assertEqual(client.pass_calls, [])
        self.assertEqual(client.commands, ["AUTH XOAUTH2"])
        self.assertEqual(len(client.lines), 1)
        decoded_payload = base64.b64decode(client.lines[0]).decode("utf-8")
        self.assertIn("user=user@example.com", decoded_payload)
        self.assertIn("auth=Bearer oauth-access-token", decoded_payload)


if __name__ == "__main__":
    unittest.main()
