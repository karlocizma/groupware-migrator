"""Tests for LDAPAuthBackend."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.engine.ldap_auth import LDAPAuthBackend, LDAPAuthError
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


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


def _store(tmp: str) -> SQLiteStateStore:
    return SQLiteStateStore(Path(tmp) / "state.db")


class TestAuthBackendColumn(unittest.TestCase):
    def test_local_user_has_local_backend(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            user_id = store.create_user(
                email="local@example.com",
                password_hash=hash_password("password"),
            )
            user = store.get_user_by_id(user_id)
            self.assertEqual(user["auth_backend"], "local")

    def test_ldap_user_has_ldap_backend(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            user_id = store.create_user(
                email="ldap@example.com",
                password_hash="!",
                auth_backend="ldap",
            )
            user = store.get_user_by_id(user_id)
            self.assertEqual(user["auth_backend"], "ldap")

    def test_get_user_by_email_includes_auth_backend(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.create_user(
                email="user@example.com",
                password_hash="!",
                auth_backend="ldap",
            )
            user = store.get_user_by_email("user@example.com")
            self.assertEqual(user["auth_backend"], "ldap")


def _authed_client(app, email: str, password: str) -> TestClient:
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


class TestLoginFlow(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        self.store: SQLiteStateStore = self.app.state.state_store
        self.store.create_user(
            email="local@example.com",
            password_hash=hash_password("localpass"),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_local_user_login_unaffected(self):
        client = TestClient(self.app)
        resp = client.post("/auth/login", json={"email": "local@example.com", "password": "localpass"})
        self.assertEqual(resp.status_code, 200)

    def test_local_user_wrong_password(self):
        client = TestClient(self.app)
        resp = client.post("/auth/login", json={"email": "local@example.com", "password": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_existing_ldap_user_success(self):
        self.store.create_user(
            email="ldap@example.com",
            password_hash="!",
            auth_backend="ldap",
        )
        client = TestClient(self.app)
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.api.routers.auth_router.LDAPAuthBackend") as MockBackend:
                instance = MockBackend.return_value
                instance.is_configured.return_value = True
                instance.authenticate.return_value = {
                    "email": "ldap@example.com",
                    "display_name": "LDAP User",
                }
                resp = client.post("/auth/login", json={"email": "ldap@example.com", "password": "adpass"})
        self.assertEqual(resp.status_code, 200)

    def test_existing_ldap_user_wrong_password(self):
        self.store.create_user(
            email="ldap@example.com",
            password_hash="!",
            auth_backend="ldap",
        )
        client = TestClient(self.app)
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.api.routers.auth_router.LDAPAuthBackend") as MockBackend:
                instance = MockBackend.return_value
                instance.is_configured.return_value = True
                instance.authenticate.return_value = None
                resp = client.post("/auth/login", json={"email": "ldap@example.com", "password": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_auto_provision_on_first_ldap_login(self):
        client = TestClient(self.app)
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com", "LDAP_DEFAULT_ROLE": "operator"}):
            with patch("groupware_migrator.api.routers.auth_router.LDAPAuthBackend") as MockBackend:
                instance = MockBackend.return_value
                instance.is_configured.return_value = True
                instance.authenticate.return_value = {
                    "email": "newuser@example.com",
                    "display_name": "New User",
                }
                resp = client.post("/auth/login", json={"email": "newuser@example.com", "password": "adpass"})
        self.assertEqual(resp.status_code, 200)
        user = self.store.get_user_by_email("newuser@example.com")
        self.assertIsNotNone(user)
        self.assertEqual(user["auth_backend"], "ldap")
        self.assertEqual(user["role"], "operator")

    def test_auto_provision_default_role(self):
        client = TestClient(self.app)
        env = {k: v for k, v in os.environ.items() if k != "LDAP_DEFAULT_ROLE"}
        env["LDAP_HOST"] = "ldap.example.com"
        with patch.dict(os.environ, env, clear=True):
            with patch("groupware_migrator.api.routers.auth_router.LDAPAuthBackend") as MockBackend:
                instance = MockBackend.return_value
                instance.is_configured.return_value = True
                instance.authenticate.return_value = {
                    "email": "newuser2@example.com",
                    "display_name": "New User 2",
                }
                client.post("/auth/login", json={"email": "newuser2@example.com", "password": "pass"})
        user = self.store.get_user_by_email("newuser2@example.com")
        self.assertEqual(user["role"], "operator")

    def test_ldap_server_down_returns_503(self):
        self.store.create_user(
            email="ldap@example.com",
            password_hash="!",
            auth_backend="ldap",
        )
        client = TestClient(self.app, raise_server_exceptions=False)
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.api.routers.auth_router.LDAPAuthBackend") as MockBackend:
                instance = MockBackend.return_value
                instance.is_configured.return_value = True
                instance.authenticate.side_effect = LDAPAuthError("Connection refused")
                resp = client.post("/auth/login", json={"email": "ldap@example.com", "password": "pass"})
        self.assertEqual(resp.status_code, 503)

    def test_unknown_user_no_ldap_returns_401(self):
        client = TestClient(self.app)
        env = {k: v for k, v in os.environ.items() if k != "LDAP_HOST"}
        with patch.dict(os.environ, env, clear=True):
            resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": "pass"})
        self.assertEqual(resp.status_code, 401)


class TestLDAPGuards(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        self.store: SQLiteStateStore = self.app.state.state_store
        self.store.create_user(
            email="ldapuser@example.com",
            password_hash="!",
            auth_backend="ldap",
        )
        self.store.create_user(
            email="localuser@example.com",
            password_hash=hash_password("localpass"),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _ldap_client(self) -> TestClient:
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch.dict(os.environ, {"LDAP_HOST": "ldap.example.com"}):
            with patch("groupware_migrator.api.routers.auth_router.LDAPAuthBackend") as MockBackend:
                instance = MockBackend.return_value
                instance.is_configured.return_value = True
                instance.authenticate.return_value = {
                    "email": "ldapuser@example.com",
                    "display_name": "LDAP User",
                }
                resp = client.post("/auth/login", json={"email": "ldapuser@example.com", "password": "adpass"})
        assert resp.status_code == 200, resp.text
        return client

    def test_change_password_blocked_for_ldap_user(self):
        client = self._ldap_client()
        resp = client.post(
            "/auth/change-password",
            json={"current_password": "adpass", "new_password": "newpass123"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("LDAP", resp.json()["detail"])

    def test_totp_setup_blocked_for_ldap_user(self):
        client = self._ldap_client()
        resp = client.get("/auth/totp/setup")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("LDAP", resp.json()["detail"])

    def test_change_password_allowed_for_local_user(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.post("/auth/login", json={"email": "localuser@example.com", "password": "localpass"})
        assert resp.status_code == 200
        resp2 = client.post(
            "/auth/change-password",
            json={"current_password": "localpass", "new_password": "newpass123"},
        )
        self.assertEqual(resp2.status_code, 200)
