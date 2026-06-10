from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import imaplib
import logging
import re
import ssl
from typing import Iterable
from groupware_migrator.connectors.auth import (
    build_xoauth2_string,
    resolve_oauth_access_token,
)

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.engine.idempotency import extract_message_id
from groupware_migrator.models import (
    AuthMode,
    ConnectionConfig,
    DestinationProtocol,
    MailboxSnapshot,
    SourceMessage,
    SourceProtocol,
    TlsProfile,
)

logger = logging.getLogger(__name__)

_FLAGS_PATTERN = re.compile(r"FLAGS \((?P<flags>[^\)]*)\)")
_INTERNALDATE_PATTERN = re.compile(r'INTERNALDATE "(?P<date>[^"]+)"')
_APPENDUID_PATTERN = re.compile(r"APPENDUID \d+ (?P<uid>\d+)")


@dataclass(slots=True)
class ImapListEntry:
    flags: set[str]
    delimiter: str | None
    mailbox: str


def _quote_mailbox(mailbox: str) -> str:
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _decode_mailbox_name(mailbox: str) -> str:
    if not mailbox:
        return mailbox
    try:
        return mailbox.encode("ascii").decode("imap4-utf-7")
    except Exception:
        return mailbox


def _parse_atom(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        inner = value[1:-1]
        inner = inner.replace(r"\\", "\\").replace(r"\"", '"')
        return inner
    return value


def _split_first_token(payload: str) -> tuple[str, str]:
    payload = payload.strip()
    if not payload:
        return "", ""
    if payload[0] != '"':
        token, _, remainder = payload.partition(" ")
        return token.strip(), remainder.strip()

    escaped = False
    for index in range(1, len(payload)):
        char = payload[index]
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == '"' and not escaped:
            token = payload[: index + 1]
            remainder = payload[index + 1 :].strip()
            return token, remainder
        escaped = False
    return payload, ""


def _parse_list_entry(entry: bytes) -> ImapListEntry | None:
    decoded = entry.decode("utf-8", errors="replace").strip()
    if not decoded.startswith("("):
        return None
    flags_end_index = decoded.find(")")
    if flags_end_index <= 0:
        return None

    flags_text = decoded[1:flags_end_index].strip()
    flags = {flag.strip().casefold() for flag in flags_text.split() if flag.strip()}

    remainder = decoded[flags_end_index + 1 :].strip()
    delimiter_token, mailbox_token = _split_first_token(remainder)
    if not delimiter_token or not mailbox_token:
        return None

    delimiter_clean = _parse_atom(delimiter_token)
    delimiter = None if delimiter_clean.upper() == "NIL" else delimiter_clean

    mailbox_raw = _parse_atom(mailbox_token)
    mailbox = _decode_mailbox_name(mailbox_raw)
    if mailbox.upper() == "NIL":
        mailbox = ""
    return ImapListEntry(flags=flags, delimiter=delimiter, mailbox=mailbox)


def _uid_lte(first: str, second: str) -> bool:
    if first.isdigit() and second.isdigit():
        return int(first) <= int(second)
    return first <= second


def _parse_flags(metadata: bytes) -> set[str]:
    decoded = metadata.decode("utf-8", errors="ignore")
    match = _FLAGS_PATTERN.search(decoded)
    if not match:
        return set()
    raw_flags = match.group("flags").strip()
    if not raw_flags:
        return set()
    return set(raw_flags.split())


def _parse_internal_date(metadata: bytes) -> datetime | None:
    decoded = metadata.decode("utf-8", errors="ignore")
    match = _INTERNALDATE_PATTERN.search(decoded)
    if not match:
        return None
    value = match.group("date")
    try:
        return datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


def _append_uid_from_response(payload: list[bytes] | None) -> str | None:
    if not payload:
        return None
    for chunk in payload:
        decoded = chunk.decode("utf-8", errors="ignore")
        match = _APPENDUID_PATTERN.search(decoded)
        if match:
            return match.group("uid")
    return None


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


class _ImapConnectorBase:
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self._hierarchy_delimiter: str = "/"
    def _authenticate(self, client: imaplib.IMAP4) -> None:
        if self.config.auth_mode is AuthMode.OAUTH2:
            access_token = resolve_oauth_access_token(self.config)
            xoauth2_payload = build_xoauth2_string(self.config.username, access_token)
            try:
                status, response = client.authenticate(
                    "XOAUTH2",
                    lambda _: xoauth2_payload.encode("utf-8"),
                )
            except Exception as exc:
                raise RuntimeError(f"IMAP XOAUTH2 authentication failed: {exc}") from exc
            if status != "OK":
                raise RuntimeError(
                    f"IMAP XOAUTH2 authentication failed with status {status}: {response}"
                )
            return

        if not self.config.password:
            raise ValueError(
                "Password authentication selected but no password is configured."
            )
        try:
            client.login(self.config.username, self.config.password)
        except Exception as exc:
            raise RuntimeError(f"IMAP login failed: {exc}") from exc

    @contextmanager
    def _connect(self):
        if self.config.use_ssl:
            client = imaplib.IMAP4_SSL(
                host=self.config.host,
                port=self.config.port,
                timeout=self.config.timeout_seconds,
                ssl_context=_build_ssl_context(self.config),
            )
        else:
            client = imaplib.IMAP4(
                host=self.config.host,
                port=self.config.port,
                timeout=self.config.timeout_seconds,
            )
        try:
            self._authenticate(client)
            yield client
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _discover_hierarchy_delimiter(self, client: imaplib.IMAP4) -> str:
        status, payload = client.list("", "")
        if status == "OK" and payload:
            for raw_entry in payload:
                if not raw_entry:
                    continue
                parsed_entry = _parse_list_entry(raw_entry)
                if parsed_entry and parsed_entry.delimiter:
                    self._hierarchy_delimiter = parsed_entry.delimiter
                    return self._hierarchy_delimiter
        self._hierarchy_delimiter = "/"
        return self._hierarchy_delimiter

    def validate(self) -> None:
        with self._connect() as client:
            status, _ = client.noop()
            if status != "OK":
                raise RuntimeError("IMAP NOOP failed during validation.")
            self._discover_hierarchy_delimiter(client)
        logger.debug("IMAP source connection validated: %s@%s", self.config.username, self.config.host)


class ImapSourceConnector(_ImapConnectorBase, SourceConnector):
    protocol = SourceProtocol.IMAP

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        snapshots: list[MailboxSnapshot] = []
        with self._connect() as client:
            status, payload = client.list()
            if status != "OK" or not payload:
                return snapshots

            discovered_delimiter: str | None = None
            for mailbox_entry in payload:
                if not mailbox_entry:
                    continue
                parsed = _parse_list_entry(mailbox_entry)
                if not parsed:
                    continue
                if parsed.delimiter and not discovered_delimiter:
                    discovered_delimiter = parsed.delimiter
                if "\\noselect" in parsed.flags:
                    continue
                if not parsed.mailbox:
                    continue

                message_count = self._count_messages(client, parsed.mailbox)
                snapshots.append(
                    MailboxSnapshot(name=parsed.mailbox, estimated_messages=message_count)
                )

            if discovered_delimiter:
                self._hierarchy_delimiter = discovered_delimiter
        return snapshots

    def estimate_pending_messages(
        self, mailbox: str, resume_from: str | None
    ) -> int | None:
        if resume_from is None:
            return None
        with self._connect() as client:
            status, _ = client.select(_quote_mailbox(mailbox), readonly=True)
            if status != "OK":
                return None
            try:
                search_status, search_data = client.uid("SEARCH", None, "ALL")
                if search_status != "OK" or not search_data or not search_data[0]:
                    return 0
                uids = [
                    token.decode("utf-8", errors="ignore")
                    for token in search_data[0].split()
                ]
                return sum(1 for uid in uids if not _uid_lte(uid, resume_from))
            finally:
                try:
                    client.close()
                except Exception:
                    pass

    def _count_messages(self, client: imaplib.IMAP4, mailbox: str) -> int:
        status, _ = client.select(_quote_mailbox(mailbox), readonly=True)
        if status != "OK":
            return 0
        try:
            search_status, search_data = client.search(None, "ALL")
            if search_status != "OK" or not search_data or not search_data[0]:
                return 0
            return len(search_data[0].split())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def iter_messages(
        self, mailbox: str, resume_from: str | None = None
    ) -> Iterable[SourceMessage]:
        with self._connect() as client:
            status, _ = client.select(_quote_mailbox(mailbox), readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select mailbox: {mailbox}")

            search_status, search_data = client.uid("SEARCH", None, "ALL")
            if search_status != "OK" or not search_data or not search_data[0]:
                return
            uids = [
                token.decode("utf-8", errors="ignore") for token in search_data[0].split()
            ]

            for uid in uids:
                if resume_from and _uid_lte(uid, resume_from):
                    continue

                fetch_status, fetch_data = client.uid(
                    "FETCH", uid, "(RFC822 FLAGS INTERNALDATE)"
                )
                if fetch_status != "OK" or not fetch_data:
                    continue

                metadata: bytes | None = None
                raw_message: bytes | None = None
                for chunk in fetch_data:
                    if not isinstance(chunk, tuple):
                        continue
                    if isinstance(chunk[0], bytes):
                        metadata = chunk[0]
                    if isinstance(chunk[1], bytes):
                        raw_message = chunk[1]
                if not raw_message:
                    continue

                yield SourceMessage(
                    source_mailbox=mailbox,
                    source_id=uid,
                    raw_message=raw_message,
                    flags=_parse_flags(metadata or b""),
                    internal_date=_parse_internal_date(metadata or b""),
                    message_id=extract_message_id(raw_message),
                )

            try:
                client.close()
            except Exception:
                pass


class ImapDestinationConnector(_ImapConnectorBase, DestinationConnector):
    protocol = DestinationProtocol.IMAP

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        self._ensured_mailboxes: set[str] = set()

    def _canonical_path_parts(self, mailbox: str) -> list[str]:
        if "/" in mailbox:
            return [chunk for chunk in mailbox.split("/") if chunk]
        return [mailbox] if mailbox else []

    def _mailbox_path_parts(self, mailbox: str, *, delimiter: str) -> list[str]:
        parts = self._canonical_path_parts(mailbox)
        current = ""
        results: list[str] = []
        for chunk in parts:
            current = chunk if not current else f"{current}{delimiter}{chunk}"
            results.append(current)
        return results

    def _create_mailbox_if_missing(self, client: imaplib.IMAP4, mailbox: str) -> None:
        if mailbox.casefold() == "inbox":
            return
        status, payload = client.create(_quote_mailbox(mailbox))
        if status == "OK":
            return
        raw = " ".join(
            chunk.decode("utf-8", errors="ignore") for chunk in payload or [] if chunk
        ).lower()
        if "exists" in raw or "already" in raw:
            return
        raise RuntimeError(f"Unable to create destination mailbox '{mailbox}': {payload}")

    def _format_mailbox(self, mailbox: str, *, delimiter: str) -> str:
        parts = self._canonical_path_parts(mailbox.strip())
        return delimiter.join(parts)

    def ensure_mailbox(self, mailbox: str) -> None:
        mailbox = mailbox.strip()
        if not mailbox:
            raise ValueError("Destination mailbox must not be empty.")
        if mailbox in self._ensured_mailboxes:
            return
        with self._connect() as client:
            delimiter = self._discover_hierarchy_delimiter(client)
            for mailbox_part in self._mailbox_path_parts(mailbox, delimiter=delimiter):
                self._create_mailbox_if_missing(client, mailbox_part)
            self._ensured_mailboxes.add(mailbox)

    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ) -> str | None:
        mailbox = mailbox.strip()
        if not mailbox:
            raise ValueError("Destination mailbox must not be empty.")

        flag_literal = None
        if flags:
            flag_literal = f"({' '.join(sorted(flags))})"

        internal_date_literal = None
        if internal_date is not None:
            if internal_date.tzinfo is None:
                internal_date = internal_date.astimezone()
            internal_date_literal = internal_date.strftime("%d-%b-%Y %H:%M:%S %z")

        with self._connect() as client:
            delimiter = self._discover_hierarchy_delimiter(client)
            destination_mailbox = self._format_mailbox(mailbox, delimiter=delimiter)
            status, payload = client.append(
                _quote_mailbox(destination_mailbox),
                flag_literal,
                internal_date_literal,
                raw_message,
            )
            if status != "OK":
                raise RuntimeError(
                    f"Failed to append message to {destination_mailbox}: {payload}"
                )
            return _append_uid_from_response(payload)