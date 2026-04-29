import unittest
from unittest.mock import patch

from groupware_migrator.connectors.auth import (
    build_xoauth2_string,
    resolve_oauth_access_token,
)
from groupware_migrator.models import AuthMode, ConnectionConfig


class _FakeHttpResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestConnectionConfigAuth(unittest.TestCase):
    def test_password_mode_requires_password(self):
        with self.assertRaises(ValueError):
            ConnectionConfig.from_dict(
                {
                    "host": "imap.example.com",
                    "username": "user@example.com",
                },
                default_port=993,
            )

    def test_oauth_mode_allows_missing_password_and_redacts_secrets(self):
        config = ConnectionConfig.from_dict(
            {
                "host": "imap.example.com",
                "username": "user@example.com",
                "auth_mode": "oauth2",
                "oauth_access_token": "access-token",
                "oauth_refresh_token": "refresh-token",
                "oauth_client_id": "client-id",
                "oauth_client_secret": "client-secret",
                "oauth_token_url": "https://oauth.example.com/token",
                "oauth_scope": "mail.read",
            },
            default_port=993,
        )
        self.assertEqual(config.auth_mode, AuthMode.OAUTH2)
        self.assertIsNone(config.password)

        redacted = config.to_dict(redact_password=True)
        self.assertEqual(redacted["oauth_access_token"], "***redacted***")
        self.assertEqual(redacted["oauth_refresh_token"], "***redacted***")
        self.assertEqual(redacted["oauth_client_secret"], "***redacted***")


class TestOAuthHelpers(unittest.TestCase):
    def test_build_xoauth2_string(self):
        payload = build_xoauth2_string("user@example.com", "token-value")
        self.assertEqual(
            payload,
            "user=user@example.com\x01auth=Bearer token-value\x01\x01",
        )

    def test_resolve_uses_explicit_access_token(self):
        config = ConnectionConfig(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            auth_mode=AuthMode.OAUTH2,
            oauth_access_token="access-token",
        )
        token = resolve_oauth_access_token(config)
        self.assertEqual(token, "access-token")

    def test_resolve_refreshes_when_access_token_missing(self):
        config = ConnectionConfig(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            auth_mode=AuthMode.OAUTH2,
            oauth_refresh_token="refresh-token",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            oauth_token_url="https://oauth.example.com/token",
            oauth_scope="mail.read",
        )

        with patch(
            "groupware_migrator.connectors.auth.urlopen",
            return_value=_FakeHttpResponse(b"{\"access_token\":\"fresh-access-token\"}"),
        ) as mock_urlopen:
            token = resolve_oauth_access_token(config)

        self.assertEqual(token, "fresh-access-token")
        request = mock_urlopen.call_args.args[0]
        payload = request.data.decode("utf-8")
        self.assertIn("grant_type=refresh_token", payload)
        self.assertIn("refresh_token=refresh-token", payload)

    def test_resolve_refresh_requires_required_fields(self):
        config = ConnectionConfig(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            auth_mode=AuthMode.OAUTH2,
        )
        with self.assertRaises(ValueError):
            resolve_oauth_access_token(config)


if __name__ == "__main__":
    unittest.main()
