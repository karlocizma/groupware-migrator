"""Tests for email notification preferences in the state store."""
from __future__ import annotations

import os
import smtplib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.engine.mailer import MailDeliveryManager
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _store(tmp: str) -> SQLiteStateStore:
    return SQLiteStateStore(Path(tmp) / "state.db")


def _user(store: SQLiteStateStore) -> str:
    return store.create_user(
        email="user@example.com",
        password_hash=hash_password("password"),
    )


def _make_request():
    from groupware_migrator.models import MigrationRequest
    from groupware_migrator.models.domain import (
        ConnectionConfig, DestinationEndpoint, DestinationProtocol,
        MigrationOptions, SourceEndpoint, SourceProtocol, WorkloadType,
    )
    return MigrationRequest(
        source=SourceEndpoint(
            protocol=SourceProtocol.IMAP,
            connection=ConnectionConfig(host="src", port=993, username="u", password="p"),
        ),
        destination=DestinationEndpoint(
            protocol=DestinationProtocol.IMAP,
            connection=ConnectionConfig(host="dst", port=993, username="u", password="p"),
        ),
        workload=WorkloadType.MAIL,
        options=MigrationOptions(),
    )


class TestNotificationPrefs(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = _store(self._tmp.name)
        self.user_id = _user(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_all_false(self):
        prefs = self.store.get_notification_prefs(self.user_id)
        self.assertFalse(prefs["on_completed"])
        self.assertFalse(prefs["on_failed"])
        self.assertFalse(prefs["on_cancelled"])

    def test_set_and_get(self):
        self.store.set_notification_prefs(
            self.user_id,
            on_completed=True,
            on_failed=True,
            on_cancelled=False,
        )
        prefs = self.store.get_notification_prefs(self.user_id)
        self.assertTrue(prefs["on_completed"])
        self.assertTrue(prefs["on_failed"])
        self.assertFalse(prefs["on_cancelled"])

    def test_upsert_updates_existing(self):
        self.store.set_notification_prefs(
            self.user_id,
            on_completed=True,
            on_failed=False,
            on_cancelled=False,
        )
        self.store.set_notification_prefs(
            self.user_id,
            on_completed=False,
            on_failed=True,
            on_cancelled=True,
        )
        prefs = self.store.get_notification_prefs(self.user_id)
        self.assertFalse(prefs["on_completed"])
        self.assertTrue(prefs["on_failed"])
        self.assertTrue(prefs["on_cancelled"])

    def test_unknown_user_returns_defaults(self):
        prefs = self.store.get_notification_prefs("nonexistent-id")
        self.assertFalse(prefs["on_completed"])
        self.assertFalse(prefs["on_failed"])
        self.assertFalse(prefs["on_cancelled"])


class TestMailDeliveryManagerIsConfigured(unittest.TestCase):
    def test_not_configured_when_no_host(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            env = {k: v for k, v in os.environ.items() if k != "SMTP_HOST"}
            with patch.dict(os.environ, env, clear=True):
                self.assertFalse(mgr.is_configured())

    def test_configured_when_host_set(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
                self.assertTrue(mgr.is_configured())


class TestMailDeliveryManagerFire(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = _store(self._tmp.name)
        self.user_id = _user(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_noop_when_not_configured(self):
        mgr = MailDeliveryManager(self.store)
        env = {k: v for k, v in os.environ.items() if k != "SMTP_HOST"}
        with patch.dict(os.environ, env, clear=True):
            with patch("groupware_migrator.engine.mailer.threading.Thread") as mock_thread:
                mgr.fire(event_type="job.completed", job_row={"status": "completed"}, user_id=self.user_id)
                mock_thread.assert_not_called()

    def test_noop_when_user_id_is_none(self):
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("groupware_migrator.engine.mailer.threading.Thread") as mock_thread:
                mgr.fire(event_type="job.completed", job_row={"status": "completed"}, user_id=None)
                mock_thread.assert_not_called()

    def test_noop_when_toggle_off(self):
        self.store.set_notification_prefs(
            self.user_id, on_completed=False, on_failed=False, on_cancelled=False
        )
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("groupware_migrator.engine.mailer.threading.Thread") as mock_thread:
                mgr.fire(event_type="job.completed", job_row={"status": "completed"}, user_id=self.user_id)
                mock_thread.assert_not_called()

    def test_fires_thread_when_toggle_on(self):
        self.store.set_notification_prefs(
            self.user_id, on_completed=True, on_failed=False, on_cancelled=False
        )
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("groupware_migrator.engine.mailer.threading.Thread") as mock_thread:
                mock_thread.return_value = MagicMock()
                mgr.fire(
                    event_type="job.completed",
                    job_row={"status": "completed", "job_name": "test"},
                    user_id=self.user_id,
                )
                mock_thread.assert_called_once()
                _, kwargs = mock_thread.call_args
                self.assertTrue(kwargs.get("daemon"))
                self.assertEqual(kwargs.get("target"), mgr._deliver)


class TestMailDeliveryManagerSendTest(unittest.TestCase):
    def test_send_test_starttls(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            env = {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "user@example.com",
                "SMTP_PASSWORD": "secret",
                "SMTP_TLS": "starttls",
            }
            with patch.dict(os.environ, env):
                with patch("smtplib.SMTP") as mock_smtp_cls:
                    mock_conn = MagicMock()
                    mock_smtp_cls.return_value.__enter__ = lambda s: mock_conn
                    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
                    mgr.send_test(to_address="admin@example.com")
                    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)

    def test_send_test_ssl(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            env = {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "465",
                "SMTP_TLS": "ssl",
            }
            with patch.dict(os.environ, env):
                with patch("smtplib.SMTP_SSL") as mock_ssl_cls:
                    mock_conn = MagicMock()
                    mock_ssl_cls.return_value.__enter__ = lambda s: mock_conn
                    mock_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)
                    mgr.send_test(to_address="admin@example.com")
                    mock_ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=10)

    def test_send_test_raises_on_smtp_error(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
                with patch("smtplib.SMTP") as mock_smtp_cls:
                    mock_smtp_cls.side_effect = ConnectionRefusedError("refused")
                    with self.assertRaises(ConnectionRefusedError):
                        mgr.send_test(to_address="admin@example.com")


def _authed_client(app, email="user@example.com", password="password123") -> TestClient:
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


class TestNotificationPrefsAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        store: SQLiteStateStore = self.app.state.state_store
        store.create_user(
            email="user@example.com",
            password_hash=hash_password("password123"),
        )
        self.client = _authed_client(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_get_defaults(self):
        resp = self.client.get("/auth/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["on_completed"])
        self.assertFalse(data["on_failed"])
        self.assertFalse(data["on_cancelled"])

    def test_patch_updates_prefs(self):
        resp = self.client.patch(
            "/auth/notifications",
            json={"on_completed": True, "on_failed": True, "on_cancelled": False},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["on_completed"])
        self.assertTrue(data["on_failed"])
        self.assertFalse(data["on_cancelled"])

    def test_patch_then_get_persists(self):
        self.client.patch(
            "/auth/notifications",
            json={"on_completed": False, "on_failed": True, "on_cancelled": True},
        )
        resp = self.client.get("/auth/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["on_completed"])
        self.assertTrue(data["on_failed"])
        self.assertTrue(data["on_cancelled"])

    def test_requires_auth(self):
        bare = TestClient(self.app, raise_server_exceptions=True)
        resp = bare.get("/auth/notifications")
        self.assertEqual(resp.status_code, 401)


class TestSmtpTestEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        store: SQLiteStateStore = self.app.state.state_store
        store.create_user(
            email="admin@example.com",
            password_hash=hash_password("adminpass"),
            is_admin=True,
        )
        self.client = _authed_client(self.app, email="admin@example.com", password="adminpass")

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_503_when_not_configured(self):
        env = {k: v for k, v in os.environ.items() if k != "SMTP_HOST"}
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post("/api/admin/smtp/test")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("SMTP_HOST", resp.json()["detail"])

    def test_returns_200_on_success(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("groupware_migrator.engine.mailer._send_smtp"):
                resp = self.client.post("/api/admin/smtp/test")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["sent_to"], "admin@example.com")

    def test_returns_502_on_smtp_error(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch(
                "groupware_migrator.engine.mailer._send_smtp",
                side_effect=smtplib.SMTPException("connection failed"),
            ):
                resp = self.client.post("/api/admin/smtp/test")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("SMTP error", resp.json()["detail"])
