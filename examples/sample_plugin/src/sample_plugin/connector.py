"""Minimal example connector plugin for groupware-migrator.

Copy this file as a starting point. Register your connector classes
in pyproject.toml under the entry-point groups shown in this package's
pyproject.toml.
"""
from __future__ import annotations

from typing import Iterable

from groupware_migrator.sdk import (
    CollectionSnapshot,
    ConnectionConfig,
    DestinationConnector,
    MailboxSnapshot,
    SourceConnector,
    SourceMessage,
)


class SampleSourceConnector(SourceConnector):
    """Stub source connector for the 'sample' protocol."""

    protocol = "sample"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        return []

    def iter_messages(
        self, mailbox: str, resume_from: str | None = None
    ) -> Iterable[SourceMessage]:
        return iter([])


class SampleDestinationConnector(DestinationConnector):
    """Stub destination connector for the 'sample' protocol."""

    protocol = "sample"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def ensure_mailbox(self, mailbox: str) -> None:
        pass

    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ) -> str | None:
        return None
