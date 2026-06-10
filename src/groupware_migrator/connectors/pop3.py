from __future__ import annotations
import base64

from contextlib import contextmanager
import logging
import poplib
import ssl
from typing import Iterable
from groupware_migrator.connectors.auth import (
    build_xoauth2_string,
    resolve_oauth_access_token,
)

from groupware_migrator.connectors.base import SourceConnector
from groupware_migrator.engine.idempotency import extract_message_id
from groupware_migrator.models import (
    AuthMode,
    ConnectionConfig,
    MailboxSnapshot,
    SourceMessage,
    SourceProtocol,
    TlsProfile,
)


logger = logging.getLogger(__name__)


def _build_ssl_context(config: ConnectionConfig) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if config.tls_profile is TlsProfile.MODERN:
        if hasattr(ssl, "TLSVersion"):
            context.minimum_version = ssl.TLSVersion.TLSv1_2
    elif config.tls_profile is TlsProfile.COMPATIBILITY:
        if hasattr(ssl, "TLSVersion"):
            try:
                context.minimum_version = ssl.TLSVersion.TLSv1
            except ValueError:
                context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class Pop3SourceConnector(SourceConnector):
    protocol = SourceProtocol.POP3

    def __init__(self, config: ConnectionConfig):
        self.config = config
    def _authenticate(self, client: poplib.POP3) -> None:
        if self.config.auth_mode is AuthMode.PASSWORD:
            if not self.config.password:
                raise ValueError(
                    "Password authentication selected but no password is configured."
                )
            client.user(self.config.username)
            client.pass_(self.config.password)
            return

        access_token = resolve_oauth_access_token(self.config)
        xoauth2_string = build_xoauth2_string(self.config.username, access_token)
        xoauth2_payload = base64.b64encode(xoauth2_string.encode("utf-8")).decode(
            "ascii"
        )
        try:
            client._putcmd("AUTH XOAUTH2")
            challenge = client._getresp()
            if challenge.startswith(b"+OK"):
                return
            if not challenge.startswith(b"+"):
                raise poplib.error_proto(challenge)
            client._putline(xoauth2_payload.encode("ascii"))
            auth_response = client._getresp()
            if not auth_response.startswith(b"+OK"):
                raise poplib.error_proto(auth_response)
        except poplib.error_proto as exc:
            raise RuntimeError(f"POP3 XOAUTH2 authentication failed: {exc}") from exc

    @contextmanager
    def _connect(self):
        if self.config.use_ssl:
            client = poplib.POP3_SSL(
                host=self.config.host,
                port=self.config.port,
                timeout=self.config.timeout_seconds,
                context=_build_ssl_context(self.config),
            )
        else:
            client = poplib.POP3(
                host=self.config.host,
                port=self.config.port,
                timeout=self.config.timeout_seconds,
            )
        try:
            self._authenticate(client)
            yield client
        finally:
            try:
                client.quit()
            except Exception:
                pass

    def validate(self) -> None:
        with self._connect() as client:
            client.stat()
        logger.debug("POP3 source connection validated: %s@%s", self.config.username, self.config.host)

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        with self._connect() as client:
            message_count, _ = client.stat()
            return [MailboxSnapshot(name="INBOX", estimated_messages=message_count)]

    def estimate_pending_messages(
        self, mailbox: str, resume_from: str | None
    ) -> int | None:
        if resume_from is None:
            return None
        if mailbox.upper() != "INBOX":
            raise ValueError("POP3 source supports only the INBOX mailbox.")

        with self._connect() as client:
            _, list_lines, _ = client.list()
            uid_lookup = self._build_uid_lookup(client)

            source_ids: list[str] = []
            for line in list_lines:
                decoded = line.decode("utf-8", errors="ignore")
                parts = decoded.split()
                if not parts or not parts[0].isdigit():
                    continue
                message_number = int(parts[0])
                source_id = uid_lookup.get(message_number, str(message_number))
                source_ids.append(source_id)

            if not source_ids:
                return 0
            if resume_from not in source_ids:
                return len(source_ids)
            return max(0, len(source_ids) - source_ids.index(resume_from) - 1)

    def _build_uid_lookup(self, client: poplib.POP3) -> dict[int, str]:
        lookup: dict[int, str] = {}
        try:
            _, lines, _ = client.uidl()
        except poplib.error_proto:
            return lookup
        for line in lines:
            decoded = line.decode("utf-8", errors="ignore")
            parts = decoded.split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            lookup[int(parts[0])] = parts[1]
        return lookup

    def iter_messages(
        self, mailbox: str, resume_from: str | None = None
    ) -> Iterable[SourceMessage]:
        if mailbox.upper() != "INBOX":
            raise ValueError("POP3 source supports only the INBOX mailbox.")

        with self._connect() as client:
            _, list_lines, _ = client.list()
            uid_lookup = self._build_uid_lookup(client)

            ordered_message_ids: list[tuple[int, str]] = []
            for line in list_lines:
                decoded = line.decode("utf-8", errors="ignore")
                parts = decoded.split()
                if not parts or not parts[0].isdigit():
                    continue
                message_number = int(parts[0])
                source_id = uid_lookup.get(message_number, str(message_number))
                ordered_message_ids.append((message_number, source_id))

            start_index = 0
            if resume_from:
                source_ids = [source_id for _, source_id in ordered_message_ids]
                if resume_from in source_ids:
                    start_index = source_ids.index(resume_from) + 1

            for message_number, source_id in ordered_message_ids[start_index:]:
                _, raw_lines, _ = client.retr(message_number)
                raw_message = b"\r\n".join(raw_lines) + b"\r\n"
                yield SourceMessage(
                    source_mailbox="INBOX",
                    source_id=source_id,
                    raw_message=raw_message,
                    flags=set(),
                    internal_date=None,
                    message_id=extract_message_id(raw_message),
                )
