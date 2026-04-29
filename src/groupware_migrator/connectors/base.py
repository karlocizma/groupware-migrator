from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from groupware_migrator.models import (
    CollectionSnapshot,
    DestinationProtocol,
    MailboxSnapshot,
    SourceItem,
    SourceMessage,
    SourceProtocol,
)


class SourceConnector(ABC):
    protocol: SourceProtocol

    @abstractmethod
    def validate(self) -> None:
        """Validate connectivity and authentication."""

    @abstractmethod
    def list_mailboxes(self) -> list[MailboxSnapshot]:
        """Return available source mailboxes and estimated message counts."""
    def list_collections(self) -> list[CollectionSnapshot]:
        """Return available source collections and estimated item counts."""
        return [
            CollectionSnapshot(
                name=snapshot.name,
                estimated_items=int(snapshot.estimated_messages),
            )
            for snapshot in self.list_mailboxes()
        ]

    @abstractmethod
    def iter_messages(
        self, mailbox: str, resume_from: str | None = None
    ) -> Iterable[SourceMessage]:
        """Yield messages from a mailbox, optionally resuming after a source ID."""

    def iter_items(
        self,
        collection: str,
        resume_from: str | None = None,
    ) -> Iterable[SourceItem]:
        """Yield generic source items, optionally resuming after a source ID."""
        for message in self.iter_messages(collection, resume_from=resume_from):
            metadata: dict[str, str] = {}
            if message.message_id:
                metadata["message_id"] = message.message_id
            if message.flags:
                metadata["flags"] = " ".join(sorted(message.flags))
            if message.internal_date is not None:
                metadata["internal_date"] = message.internal_date.isoformat()
            yield SourceItem(
                source_collection=message.source_mailbox,
                source_id=message.source_id,
                raw_payload=message.raw_message,
                content_type="message/rfc822",
                version_token=None,
                item_key=message.message_id,
                metadata=metadata,
            )

    def estimate_pending_messages(
        self,
        mailbox: str,
        resume_from: str | None,
    ) -> int | None:
        """Return estimated pending message count from a resume cursor, if cheaply available."""
        return None

    def estimate_pending_items(
        self,
        collection: str,
        resume_from: str | None,
    ) -> int | None:
        """Return estimated pending item count from a resume cursor, if cheaply available."""
        return self.estimate_pending_messages(collection, resume_from)


class DestinationConnector(ABC):
    protocol: DestinationProtocol

    @abstractmethod
    def validate(self) -> None:
        """Validate connectivity and authentication."""

    @abstractmethod
    def ensure_mailbox(self, mailbox: str) -> None:
        """Ensure a mailbox exists in destination."""
    def ensure_collection(self, collection: str) -> None:
        """Ensure a collection exists in destination."""
        self.ensure_mailbox(collection)

    @abstractmethod
    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ) -> str | None:
        """Append message to destination mailbox and return destination message ID if available."""

    def upsert_item(
        self,
        collection: str,
        source_id: str,
        raw_payload: bytes,
        *,
        metadata: dict[str, str] | None = None,
    ) -> str | None:
        """Write or update an item in a destination collection and return destination item ID if available."""
        metadata = metadata or {}
        raw_flags = metadata.get("flags", "").strip()
        flags = {flag for flag in raw_flags.split(" ") if flag} if raw_flags else None
        internal_date = None
        internal_date_raw = metadata.get("internal_date")
        if internal_date_raw:
            try:
                from datetime import datetime

                internal_date = datetime.fromisoformat(internal_date_raw)
            except Exception:
                internal_date = None
        return self.append_message(
            collection,
            raw_payload,
            flags=flags,
            internal_date=internal_date,
        )
