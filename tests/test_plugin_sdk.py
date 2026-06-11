"""Tests for Plugin/Connector SDK."""
from __future__ import annotations

import unittest
from typing import Iterable

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.engine.plugin_registry import PluginRegistry, get_registry
from groupware_migrator.models.domain import (
    ConnectionConfig,
    MailboxSnapshot,
    SourceMessage,
)


# ---------------------------------------------------------------------------
# Fake connectors used across multiple test classes
# ---------------------------------------------------------------------------

class _FakeSourceConnector(SourceConnector):
    protocol = "fake_proto"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        return []

    def iter_messages(self, mailbox: str, resume_from: str | None = None) -> Iterable[SourceMessage]:
        return iter([])


class _FakeDestinationConnector(DestinationConnector):
    protocol = "fake_proto"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def ensure_mailbox(self, mailbox: str) -> None:
        pass

    def append_message(self, mailbox: str, raw_message: bytes, *, flags=None, internal_date=None) -> str | None:
        return None


# ---------------------------------------------------------------------------
# TestPluginRegistry
# ---------------------------------------------------------------------------

class TestPluginRegistry(unittest.TestCase):
    def test_empty_registry_returns_none_for_unknown_source(self):
        reg = PluginRegistry()
        self.assertIsNone(reg.get_source("no_such_proto"))

    def test_empty_registry_returns_none_for_unknown_destination(self):
        reg = PluginRegistry()
        self.assertIsNone(reg.get_destination("no_such_proto"))

    def test_registered_source_connector_is_returned(self):
        reg = PluginRegistry()
        reg._source["fake_proto"] = _FakeSourceConnector
        self.assertIs(reg.get_source("fake_proto"), _FakeSourceConnector)

    def test_registered_destination_connector_is_returned(self):
        reg = PluginRegistry()
        reg._destination["fake_proto"] = _FakeDestinationConnector
        self.assertIs(reg.get_destination("fake_proto"), _FakeDestinationConnector)

    def test_list_plugins_returns_empty_when_no_plugins(self):
        reg = PluginRegistry()
        self.assertEqual(reg.list_plugins(), [])

    def test_list_plugins_returns_metadata(self):
        reg = PluginRegistry()
        reg._meta = [{
            "name": "my-plugin",
            "version": "1.0.0",
            "source_protocols": ["fake_proto"],
            "destination_protocols": [],
        }]
        plugins = reg.list_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "my-plugin")
        self.assertEqual(plugins[0]["source_protocols"], ["fake_proto"])

    def test_get_registry_returns_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        self.assertIs(r1, r2)


# ---------------------------------------------------------------------------
# TestSDKPublicAPI
# ---------------------------------------------------------------------------

class TestSDKPublicAPI(unittest.TestCase):
    def test_sdk_exports_source_connector(self):
        from groupware_migrator.sdk import SourceConnector as SDKSource
        from groupware_migrator.connectors.base import SourceConnector as BaseSource
        self.assertIs(SDKSource, BaseSource)

    def test_sdk_exports_destination_connector(self):
        from groupware_migrator.sdk import DestinationConnector as SDKDest
        from groupware_migrator.connectors.base import DestinationConnector as BaseDest
        self.assertIs(SDKDest, BaseDest)

    def test_sdk_exports_connection_config(self):
        from groupware_migrator.sdk import ConnectionConfig as SDKConfig
        from groupware_migrator.models.domain import ConnectionConfig as DomainConfig
        self.assertIs(SDKConfig, DomainConfig)

    def test_sdk_exports_source_item(self):
        from groupware_migrator.sdk import SourceItem
        self.assertIsNotNone(SourceItem)

    def test_sdk_exports_source_message(self):
        from groupware_migrator.sdk import SourceMessage
        self.assertIsNotNone(SourceMessage)

    def test_sdk_exports_collection_snapshot(self):
        from groupware_migrator.sdk import CollectionSnapshot
        self.assertIsNotNone(CollectionSnapshot)

    def test_sdk_exports_mailbox_snapshot(self):
        from groupware_migrator.sdk import MailboxSnapshot
        self.assertIsNotNone(MailboxSnapshot)
