"""Tests for MS Graph source connector and Phase 9 provider presets."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from groupware_migrator.connectors.factory import create_source_connector
from groupware_migrator.connectors.graph import MsGraphSourceConnector
from groupware_migrator.models import AuthMode, CollectionSnapshot, MigrationRequest, SourceProtocol
from groupware_migrator.models.domain import ConnectionConfig
from groupware_migrator.providers import get_provider_preset, get_provider_presets


def _graph_config(*, token: str = "fake-token") -> ConnectionConfig:
    return ConnectionConfig(
        host="graph.microsoft.com",
        port=443,
        username="user@example.com",
        password=None,
        use_ssl=True,
        auth_mode=AuthMode.OAUTH2,
        oauth_access_token=token,
    )


# ---------------------------------------------------------------------------
# Unit tests — connector internals
# ---------------------------------------------------------------------------

class TestMsGraphConnectorUnit(unittest.TestCase):
    def setUp(self):
        self.connector = MsGraphSourceConnector(_graph_config())

    def test_base_url_defaults_to_graph_microsoft_com(self):
        self.assertIn("graph.microsoft.com", self.connector._base)

    def test_base_url_includes_v1(self):
        self.assertIn("/v1.0", self.connector._base)

    def test_custom_sovereign_cloud_host(self):
        cfg = _graph_config()
        cfg = ConnectionConfig(
            host="graph.microsoft.us",
            port=443,
            username="u@gov.example",
            password=None,
            use_ssl=True,
            auth_mode=AuthMode.OAUTH2,
            oauth_access_token="tok",
        )
        connector = MsGraphSourceConnector(cfg)
        self.assertIn("graph.microsoft.us", connector._base)

    def test_protocol_enum_value(self):
        self.assertEqual(str(MsGraphSourceConnector.protocol), "msgraph")

    @patch.object(MsGraphSourceConnector, "_get")
    def test_validate_calls_me_endpoint(self, mock_get):
        mock_get.return_value = {"id": "u1", "mail": "u@example.com"}
        self.connector.validate()
        call_url = mock_get.call_args[0][0]
        self.assertIn("/me", call_url)

    @patch.object(MsGraphSourceConnector, "_paginate")
    def test_list_collections_returns_snapshots(self, mock_paginate):
        mock_paginate.return_value = [
            {"id": "folder-1", "displayName": "Inbox", "totalItemCount": 42},
            {"id": "folder-2", "displayName": "Sent", "totalItemCount": 17},
        ]
        snapshots = self.connector.list_collections()
        self.assertEqual(len(snapshots), 2)
        self.assertIsInstance(snapshots[0], CollectionSnapshot)
        # name is the folder ID (used for subsequent API calls)
        ids = {s.name for s in snapshots}
        self.assertIn("folder-1", ids)
        self.assertIn("folder-2", ids)
        counts = {s.estimated_items for s in snapshots}
        self.assertIn(42, counts)

    @patch.object(MsGraphSourceConnector, "_paginate")
    def test_list_mailboxes_delegates_to_list_collections(self, mock_paginate):
        mock_paginate.return_value = [
            {"id": "inbox-id", "displayName": "INBOX", "totalItemCount": 5}
        ]
        mboxes = self.connector.list_mailboxes()
        self.assertEqual(len(mboxes), 1)
        self.assertEqual(mboxes[0].name, "inbox-id")
        self.assertEqual(mboxes[0].estimated_items, 5)

    @patch.object(MsGraphSourceConnector, "_get_raw")
    @patch.object(MsGraphSourceConnector, "_paginate")
    def test_iter_items_yields_source_items(self, mock_paginate, mock_get_raw):
        mock_paginate.return_value = [
            {
                "id": "msg-001",
                "receivedDateTime": "2026-01-01T12:00:00Z",
                "internetMessageId": "<abc@example.com>",
                "changeKey": "etag-001",
            }
        ]
        mock_get_raw.return_value = b"From: sender@example.com\r\nSubject: Test\r\n\r\nBody"
        items = list(self.connector.iter_items("folder-1"))
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source_id, "msg-001")
        self.assertEqual(item.content_type, "message/rfc822")
        self.assertEqual(item.version_token, "etag-001")
        self.assertIn(b"From:", item.raw_payload)

    @patch.object(MsGraphSourceConnector, "_get_raw")
    @patch.object(MsGraphSourceConnector, "_paginate")
    def test_iter_items_skips_at_resume_from(self, mock_paginate, mock_get_raw):
        mock_paginate.return_value = [
            {"id": "aaa", "receivedDateTime": "", "internetMessageId": "", "changeKey": ""},
            {"id": "bbb", "receivedDateTime": "", "internetMessageId": "", "changeKey": ""},
        ]
        mock_get_raw.return_value = b"raw"
        items = list(self.connector.iter_items("folder-1", resume_from="aaa"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_id, "bbb")

    @patch.object(MsGraphSourceConnector, "_get_raw")
    @patch.object(MsGraphSourceConnector, "_paginate")
    def test_iter_items_handles_raw_download_error(self, mock_paginate, mock_get_raw):
        mock_paginate.return_value = [
            {"id": "broken-msg", "receivedDateTime": "", "internetMessageId": "", "changeKey": ""}
        ]
        mock_get_raw.side_effect = RuntimeError("download failed")
        items = list(self.connector.iter_items("folder-1"))
        # Should yield item with empty payload rather than crashing
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].raw_payload, b"")

    @patch.object(MsGraphSourceConnector, "_get_raw")
    @patch.object(MsGraphSourceConnector, "_paginate")
    def test_iter_messages_delegates_to_iter_items(self, mock_paginate, mock_get_raw):
        mock_paginate.return_value = [
            {"id": "m1", "receivedDateTime": "", "internetMessageId": "", "changeKey": ""}
        ]
        mock_get_raw.return_value = b"raw"
        items_a = list(self.connector.iter_items("f1"))
        items_b = list(self.connector.iter_messages("f1"))
        self.assertEqual(len(items_a), len(items_b))


# ---------------------------------------------------------------------------
# Unit tests — protocol enum and factory
# ---------------------------------------------------------------------------

class TestMsGraphProtocol(unittest.TestCase):
    def test_msgraph_in_source_protocol_enum(self):
        self.assertEqual(SourceProtocol.MSGRAPH, "msgraph")

    def test_factory_creates_msgraph_connector(self):
        request = MigrationRequest.from_dict({
            "source": {
                "protocol": "msgraph",
                "connection": {
                    "host": "graph.microsoft.com",
                    "username": "u@example.com",
                    "auth_mode": "oauth2",
                    "oauth_access_token": "tok",
                },
            },
            "destination": {
                "protocol": "imap",
                "connection": {
                    "host": "imap.example.com",
                    "username": "dest@example.com",
                    "password": "pass",
                },
            },
        })
        connector = create_source_connector(request)
        self.assertIsInstance(connector, MsGraphSourceConnector)

    def test_msgraph_accepted_as_mail_workload_source(self):
        request = MigrationRequest.from_dict({
            "workload": "mail",
            "source": {
                "protocol": "msgraph",
                "connection": {
                    "host": "graph.microsoft.com",
                    "username": "u@example.com",
                    "auth_mode": "oauth2",
                    "oauth_access_token": "tok",
                },
            },
            "destination": {
                "protocol": "imap",
                "connection": {
                    "host": "imap.example.com",
                    "username": "u@dest.example.com",
                    "password": "pass",
                },
            },
        })
        self.assertEqual(str(request.source.protocol), "msgraph")


# ---------------------------------------------------------------------------
# Unit tests — provider presets
# ---------------------------------------------------------------------------

class TestProviderPresets(unittest.TestCase):
    def test_nextcloud_preset_exists(self):
        preset = get_provider_preset("nextcloud")
        self.assertIsNotNone(preset)
        self.assertEqual(preset["id"], "nextcloud")

    def test_nextcloud_has_caldav_defaults(self):
        preset = get_provider_preset("nextcloud")
        self.assertIn("caldav", preset["source_defaults"])
        self.assertIn("carddav", preset["source_defaults"])

    def test_exchange_online_preset_exists(self):
        preset = get_provider_preset("exchange_online")
        self.assertIsNotNone(preset)
        self.assertEqual(preset["id"], "exchange_online")

    def test_exchange_online_has_msgraph_defaults(self):
        preset = get_provider_preset("exchange_online")
        self.assertIn("msgraph", preset["source_defaults"])

    def test_exchange_online_has_imap_defaults(self):
        preset = get_provider_preset("exchange_online")
        self.assertIn("imap", preset["source_defaults"])
        imap = preset["source_defaults"]["imap"]
        self.assertEqual(imap["host"], "outlook.office365.com")
        self.assertEqual(imap["auth_mode"], "oauth2")

    def test_nextcloud_auth_notes_present(self):
        preset = get_provider_preset("nextcloud")
        self.assertGreater(len(preset["auth_notes"]), 0)

    def test_all_presets_have_id_and_name(self):
        for preset in get_provider_presets():
            with self.subTest(id=preset["id"]):
                self.assertIn("id", preset)
                self.assertIn("name", preset)


if __name__ == "__main__":
    unittest.main()
