"""Tests for LDAPAuthBackend."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from groupware_migrator.engine.ldap_auth import LDAPAuthBackend, LDAPAuthError


class TestLDAPAuthBackendIsConfigured(unittest.TestCase):
    def test_not_configured_when_no_host(self):
        mgr = LDAPAuthBackend()
        env = {k: v for k, v in os.environ.items() if k != "LDAP_HOST"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(mgr.is_configured())

    def test_configured_when_host_set(self):
        mgr = LDAPAuthBackend()
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            self.assertTrue(mgr.is_configured())


class TestLDAPAuthBackendAuthenticate(unittest.TestCase):
    def _make_entry(self, dn: str, mail: str = "user@example.com", display: str = "Test User"):
        entry = MagicMock()
        entry.entry_dn = dn
        entry.__contains__ = lambda self, key: key in ("mail", "displayName")
        entry.__getitem__ = lambda self, key: MagicMock(__str__=lambda s: mail if key == "mail" else display)
        return entry

    def test_authenticate_success(self):
        mgr = LDAPAuthBackend()
        env = {
            "LDAP_HOST": "ldap.example.com",
            "LDAP_BIND_DN": "CN=svc,DC=corp,DC=example",
            "LDAP_BIND_PASSWORD": "svcpass",
            "LDAP_BASE_DN": "OU=Users,DC=corp,DC=example",
        }
        with patch.dict(os.environ, env):
            with patch("groupware_migrator.engine.ldap_auth.ldap3") as mock_ldap3:
                mock_server = MagicMock()
                mock_ldap3.Server.return_value = mock_server

                service_conn = MagicMock()
                service_conn.bind.return_value = True
                service_conn.entries = [self._make_entry("CN=user,OU=Users,DC=corp,DC=example")]

                user_conn = MagicMock()
                user_conn.bind.return_value = True

                mock_ldap3.Connection.side_effect = [service_conn, user_conn]
                mock_ldap3.SUBTREE = "SUBTREE"

                result = mgr.authenticate("user@example.com", "correctpass")

        self.assertIsNotNone(result)
        self.assertEqual(result["email"], "user@example.com")

    def test_authenticate_user_not_found_returns_none(self):
        mgr = LDAPAuthBackend()
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.engine.ldap_auth.ldap3") as mock_ldap3:
                service_conn = MagicMock()
                service_conn.bind.return_value = True
                service_conn.entries = []
                mock_ldap3.Connection.return_value = service_conn
                mock_ldap3.SUBTREE = "SUBTREE"
                mock_ldap3.Server.return_value = MagicMock()

                result = mgr.authenticate("unknown@example.com", "pass")

        self.assertIsNone(result)

    def test_authenticate_wrong_password_returns_none(self):
        mgr = LDAPAuthBackend()
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.engine.ldap_auth.ldap3") as mock_ldap3:
                service_conn = MagicMock()
                service_conn.bind.return_value = True
                service_conn.entries = [self._make_entry("CN=user,DC=corp")]

                user_conn = MagicMock()
                user_conn.bind.return_value = False

                mock_ldap3.Connection.side_effect = [service_conn, user_conn]
                mock_ldap3.SUBTREE = "SUBTREE"
                mock_ldap3.Server.return_value = MagicMock()

                result = mgr.authenticate("user@example.com", "wrongpass")

        self.assertIsNone(result)

    def test_authenticate_raises_ldap_auth_error_on_connectivity_failure(self):
        mgr = LDAPAuthBackend()
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.engine.ldap_auth.ldap3") as mock_ldap3:
                mock_ldap3.Server.side_effect = Exception("Connection refused")

                with self.assertRaises(LDAPAuthError):
                    mgr.authenticate("user@example.com", "pass")

    def test_authenticate_raises_when_service_bind_fails(self):
        mgr = LDAPAuthBackend()
        with patch.dict(os.environ, {
            "LDAP_HOST": "ldap.example.com",
            "LDAP_BIND_DN": "CN=svc,DC=corp",
            "LDAP_BIND_PASSWORD": "bad",
        }):
            with patch("groupware_migrator.engine.ldap_auth.ldap3") as mock_ldap3:
                service_conn = MagicMock()
                service_conn.bind.return_value = False
                service_conn.result = {"description": "invalidCredentials"}
                mock_ldap3.Connection.return_value = service_conn
                mock_ldap3.Server.return_value = MagicMock()
                mock_ldap3.SUBTREE = "SUBTREE"

                with self.assertRaises(LDAPAuthError):
                    mgr.authenticate("user@example.com", "pass")
