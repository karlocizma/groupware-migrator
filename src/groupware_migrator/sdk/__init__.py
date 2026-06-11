from __future__ import annotations

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.models.domain import (
    CollectionSnapshot,
    ConnectionConfig,
    MailboxSnapshot,
    SourceItem,
    SourceMessage,
)

__all__ = [
    "SourceConnector",
    "DestinationConnector",
    "ConnectionConfig",
    "SourceItem",
    "SourceMessage",
    "CollectionSnapshot",
    "MailboxSnapshot",
]
