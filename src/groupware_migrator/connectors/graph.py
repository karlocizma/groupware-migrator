"""Microsoft Graph API source connector for mail migration.

Supports migrating email from Microsoft 365 / Exchange Online using the
MS Graph REST API. Requires an OAuth2 access token with Mail.Read scope.

Connection config:
  host        — Graph endpoint base (default: graph.microsoft.com)
  username    — User's UPN / email (used for /me fallback and logging)
  auth_mode   — must be "oauth2"
  oauth_access_token   — bearer token (preferred)
  oauth_refresh_token  — alternative; exchanged on first use
  oauth_client_id / oauth_client_secret / oauth_token_url — for refresh
"""
from __future__ import annotations

import email.message
import json
import urllib.error
import urllib.request
from typing import Iterator

from groupware_migrator.connectors.auth import resolve_oauth_access_token
from groupware_migrator.connectors.base import SourceConnector
from groupware_migrator.models import AuthMode, CollectionSnapshot, SourceItem, SourceProtocol
from groupware_migrator.models.domain import ConnectionConfig

_DEFAULT_HOST = "graph.microsoft.com"
_TOP = 100


class MsGraphSourceConnector(SourceConnector):
    protocol = SourceProtocol.MSGRAPH

    def __init__(self, config: ConnectionConfig) -> None:
        self._config = config
        self._token: str | None = None
        host = (config.host or _DEFAULT_HOST).rstrip("/")
        if not host.startswith("http"):
            host = f"https://{host}"
        self._base = f"{host}/v1.0"

    def _access_token(self) -> str:
        if self._token:
            return self._token
        self._token = resolve_oauth_access_token(self._config)
        return self._token

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise PermissionError(f"MS Graph 401 — token may be expired: {url}") from exc
            raise RuntimeError(f"MS Graph request failed ({exc.code}): {url}") from exc

    def _get_raw(self, url: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "message/rfc822",
            },
        )
        with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
            return resp.read()

    def _paginate(self, url: str) -> Iterator[dict]:
        """Yield items from a paged MS Graph collection."""
        next_url: str | None = url
        while next_url:
            page = self._get(next_url)
            yield from page.get("value", [])
            next_url = page.get("@odata.nextLink")

    # ------------------------------------------------------------------
    # SourceConnector interface
    # ------------------------------------------------------------------

    def validate(self) -> None:
        self._get(f"{self._base}/me?$select=id,mail")

    def list_mailboxes(self) -> list[CollectionSnapshot]:
        return self.list_collections()

    def list_collections(self) -> list[CollectionSnapshot]:
        snapshots = []
        for folder in self._paginate(
            f"{self._base}/me/mailFolders?$select=id,displayName,totalItemCount&$top={_TOP}"
        ):
            snapshots.append(
                CollectionSnapshot(
                    name=folder["id"],
                    estimated_items=folder.get("totalItemCount", 0),
                )
            )
        return snapshots

    def iter_messages(
        self, mailbox: str, resume_from: str | None = None
    ) -> Iterator[SourceItem]:
        return self.iter_items(mailbox, resume_from)

    def iter_items(
        self, collection: str, resume_from: str | None = None
    ) -> Iterator[SourceItem]:
        url = (
            f"{self._base}/me/mailFolders/{collection}/messages"
            f"?$select=id,receivedDateTime,internetMessageId,changeKey"
            f"&$orderby=receivedDateTime+asc&$top={_TOP}"
        )
        for msg in self._paginate(url):
            msg_id = msg["id"]
            if resume_from and msg_id <= resume_from:
                continue
            # Fetch raw MIME on demand
            try:
                raw = self._get_raw(f"{self._base}/me/messages/{msg_id}/$value")
            except Exception:
                raw = b""
            yield SourceItem(
                source_collection=collection,
                source_id=msg_id,
                raw_payload=raw,
                content_type="message/rfc822",
                version_token=msg.get("changeKey", ""),
                item_key=msg.get("internetMessageId", msg_id),
                metadata={
                    "received_date_time": msg.get("receivedDateTime", ""),
                    "graph_message_id": msg_id,
                },
            )
