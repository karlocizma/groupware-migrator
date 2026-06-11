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


from groupware_migrator.models.domain import (
    DestinationEndpoint,
    DestinationProtocol,
    MigrationOptions,
    MigrationRequest,
    SourceEndpoint,
    SourceProtocol,
    WorkloadType,
)


# ---------------------------------------------------------------------------
# TestDomainModelChanges
# ---------------------------------------------------------------------------

def _src_payload(protocol: str) -> dict:
    return {
        "protocol": protocol,
        "connection": {"host": "h.example.com", "port": 443, "username": "u", "password": "p"},
    }


def _dst_payload(protocol: str) -> dict:
    return {
        "protocol": protocol,
        "connection": {"host": "h.example.com", "port": 443, "username": "u", "password": "p"},
    }


class TestDomainModelChanges(unittest.TestCase):
    def test_source_endpoint_parses_builtin_protocol(self):
        ep = SourceEndpoint.from_dict(_src_payload("imap"))
        self.assertIs(ep.protocol, SourceProtocol.IMAP)

    def test_source_endpoint_accepts_plugin_protocol_string(self):
        ep = SourceEndpoint.from_dict(_src_payload("ews"))
        self.assertEqual(ep.protocol, "ews")
        self.assertNotIsInstance(ep.protocol, SourceProtocol)

    def test_destination_endpoint_accepts_plugin_protocol_string(self):
        ep = DestinationEndpoint.from_dict(_dst_payload("graph"))
        self.assertEqual(ep.protocol, "graph")
        self.assertNotIsInstance(ep.protocol, DestinationProtocol)

    def test_to_dict_works_for_builtin_protocol(self):
        ep = SourceEndpoint.from_dict(_src_payload("imap"))
        d = ep.to_dict()
        self.assertEqual(d["protocol"], "imap")

    def test_to_dict_works_for_plugin_protocol(self):
        ep = SourceEndpoint.from_dict(_src_payload("ews"))
        d = ep.to_dict()
        self.assertEqual(d["protocol"], "ews")

    def test_workload_validation_skipped_for_plugin_protocols(self):
        req = MigrationRequest.from_dict({
            "source": _src_payload("ews"),
            "destination": _dst_payload("ews"),
            "workload": "mail",
        })
        self.assertEqual(req.workload, WorkloadType.MAIL)

    def test_workload_validation_still_runs_for_builtin_protocols(self):
        with self.assertRaises(ValueError):
            MigrationRequest.from_dict({
                "source": _src_payload("caldav"),
                "destination": _dst_payload("imap"),
                "workload": "mail",
            })


from unittest.mock import patch

from groupware_migrator.connectors.factory import (
    create_destination_connector,
    create_source_connector,
)


# ---------------------------------------------------------------------------
# TestFactoryWithPlugin
# ---------------------------------------------------------------------------

def _make_conn() -> ConnectionConfig:
    return ConnectionConfig(host="h.example.com", port=443, username="u", password="p")


def _make_source_ep(protocol: str) -> SourceEndpoint:
    return SourceEndpoint(protocol=protocol, connection=_make_conn())


def _make_dest_ep(protocol: str) -> DestinationEndpoint:
    return DestinationEndpoint(protocol=protocol, connection=_make_conn())


def _make_request(src_proto: str, dst_proto: str) -> MigrationRequest:
    return MigrationRequest(
        source=_make_source_ep(src_proto),
        destination=_make_dest_ep(dst_proto),
        workload=WorkloadType.MAIL,
        options=MigrationOptions(),
    )


class TestFactoryWithPlugin(unittest.TestCase):
    def _reg_with_source(self) -> PluginRegistry:
        reg = PluginRegistry()
        reg._source["fake_proto"] = _FakeSourceConnector
        return reg

    def _reg_with_destination(self) -> PluginRegistry:
        reg = PluginRegistry()
        reg._destination["fake_proto"] = _FakeDestinationConnector
        return reg

    def test_factory_uses_registered_source_plugin(self):
        req = _make_request("fake_proto", "imap")
        reg = self._reg_with_source()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            connector = create_source_connector(req)
        self.assertIsInstance(connector, _FakeSourceConnector)

    def test_factory_uses_registered_destination_plugin(self):
        req = _make_request("imap", "fake_proto")
        reg = self._reg_with_destination()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            connector = create_destination_connector(req)
        self.assertIsInstance(connector, _FakeDestinationConnector)

    def test_factory_raises_for_truly_unknown_source_protocol(self):
        req = _make_request("no_such_proto", "imap")
        reg = PluginRegistry()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            with self.assertRaises(ValueError):
                create_source_connector(req)

    def test_factory_raises_for_truly_unknown_destination_protocol(self):
        req = _make_request("imap", "no_such_proto")
        reg = PluginRegistry()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            with self.assertRaises(ValueError):
                create_destination_connector(req)

    def test_builtin_source_connectors_unaffected(self):
        req = _make_request("imap", "imap")
        from groupware_migrator.connectors.imap import ImapSourceConnector
        connector = create_source_connector(req)
        self.assertIsInstance(connector, ImapSourceConnector)


from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _authed_admin_client(app) -> TestClient:
    store: SQLiteStateStore = app.state.state_store
    store.create_user(
        email="admin@example.com",
        password_hash=hash_password("adminpass"),
        is_admin=True,
    )
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": "admin@example.com", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    return client


# ---------------------------------------------------------------------------
# TestAdminPluginsEndpoint
# ---------------------------------------------------------------------------

class TestAdminPluginsEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        self.client = _authed_admin_client(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_empty_list_when_no_plugins(self):
        reg = PluginRegistry()
        with patch("groupware_migrator.api.routers.admin_router.get_registry", return_value=reg):
            resp = self.client.get("/api/admin/plugins")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_returns_plugin_metadata(self):
        reg = PluginRegistry()
        reg._meta = [{
            "name": "my-plugin",
            "version": "1.2.3",
            "source_protocols": ["fake_proto"],
            "destination_protocols": [],
        }]
        with patch("groupware_migrator.api.routers.admin_router.get_registry", return_value=reg):
            resp = self.client.get("/api/admin/plugins")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "my-plugin")
        self.assertEqual(data[0]["version"], "1.2.3")
        self.assertEqual(data[0]["source_protocols"], ["fake_proto"])

    def test_requires_admin(self):
        client = TestClient(self.app)
        resp = client.get("/api/admin/plugins")
        self.assertEqual(resp.status_code, 401)
