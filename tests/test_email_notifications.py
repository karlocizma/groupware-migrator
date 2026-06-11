"""Tests for email notification preferences in the state store."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
