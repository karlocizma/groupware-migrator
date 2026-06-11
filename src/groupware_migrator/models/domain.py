from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SourceProtocol(StrEnum):
    IMAP = "imap"
    POP3 = "pop3"
    CALDAV = "caldav"
    CARDDAV = "carddav"


class DestinationProtocol(StrEnum):
    IMAP = "imap"
    CALDAV = "caldav"
    CARDDAV = "carddav"


class WorkloadType(StrEnum):
    MAIL = "mail"
    CALENDAR = "calendar"
    TASKS = "tasks"
    CONTACTS = "contacts"
    NOTES = "notes"

class TlsProfile(StrEnum):
    MODERN = "modern"
    COMPATIBILITY = "compatibility"

class AuthMode(StrEnum):
    PASSWORD = "password"
    OAUTH2 = "oauth2"
class SyncMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ConnectionConfig:
    host: str
    port: int
    username: str
    password: str | None = None
    use_ssl: bool = True
    tls_profile: TlsProfile = TlsProfile.MODERN
    auth_mode: AuthMode = AuthMode.PASSWORD
    oauth_access_token: str | None = None
    oauth_refresh_token: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_token_url: str | None = None
    oauth_scope: str | None = None
    timeout_seconds: int = 30

    @classmethod
    def from_dict(cls, payload: dict, *, default_port: int) -> "ConnectionConfig":
        tls_profile_raw = str(payload.get("tls_profile", TlsProfile.MODERN.value)).lower()
        try:
            tls_profile = TlsProfile(tls_profile_raw)
        except ValueError:
            tls_profile = TlsProfile.MODERN
        auth_mode_raw = str(payload.get("auth_mode", AuthMode.PASSWORD.value)).lower()
        try:
            auth_mode = AuthMode(auth_mode_raw)
        except ValueError:
            auth_mode = AuthMode.PASSWORD

        def optional_string(value: object | None) -> str | None:
            if value is None:
                return None
            normalized = str(value).strip()
            return normalized or None

        password = optional_string(payload.get("password"))
        if auth_mode is AuthMode.PASSWORD and not password:
            raise ValueError("Missing required field: password.")
        return cls(
            host=str(payload["host"]),
            port=int(payload.get("port", default_port)),
            username=str(payload["username"]),
            password=password,
            use_ssl=bool(payload.get("use_ssl", True)),
            tls_profile=tls_profile,
            auth_mode=auth_mode,
            oauth_access_token=optional_string(payload.get("oauth_access_token")),
            oauth_refresh_token=optional_string(payload.get("oauth_refresh_token")),
            oauth_client_id=optional_string(payload.get("oauth_client_id")),
            oauth_client_secret=optional_string(payload.get("oauth_client_secret")),
            oauth_token_url=optional_string(payload.get("oauth_token_url")),
            oauth_scope=optional_string(payload.get("oauth_scope")),
            timeout_seconds=int(payload.get("timeout_seconds", 30)),
        )

    def to_dict(self, *, redact_password: bool = False) -> dict:
        redacted_password = (
            "***redacted***" if redact_password and self.password is not None else self.password
        )
        redacted_oauth_access_token = (
            "***redacted***"
            if redact_password and self.oauth_access_token is not None
            else self.oauth_access_token
        )
        redacted_oauth_refresh_token = (
            "***redacted***"
            if redact_password and self.oauth_refresh_token is not None
            else self.oauth_refresh_token
        )
        redacted_oauth_client_secret = (
            "***redacted***"
            if redact_password and self.oauth_client_secret is not None
            else self.oauth_client_secret
        )
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": redacted_password,
            "use_ssl": self.use_ssl,
            "tls_profile": self.tls_profile.value,
            "auth_mode": self.auth_mode.value,
            "oauth_access_token": redacted_oauth_access_token,
            "oauth_refresh_token": redacted_oauth_refresh_token,
            "oauth_client_id": self.oauth_client_id,
            "oauth_client_secret": redacted_oauth_client_secret,
            "oauth_token_url": self.oauth_token_url,
            "oauth_scope": self.oauth_scope,
            "timeout_seconds": self.timeout_seconds,
        }

def _infer_workload(
    *,
    source_protocol: SourceProtocol | str,
    destination_protocol: DestinationProtocol | str,
) -> WorkloadType:
    if (
        source_protocol is SourceProtocol.CARDDAV
        or destination_protocol is DestinationProtocol.CARDDAV
    ):
        return WorkloadType.CONTACTS
    if (
        source_protocol is SourceProtocol.CALDAV
        or destination_protocol is DestinationProtocol.CALDAV
    ):
        return WorkloadType.CALENDAR
    return WorkloadType.MAIL


def _validate_workload_protocols(
    *,
    workload: WorkloadType,
    source_protocol: SourceProtocol | str,
    destination_protocol: DestinationProtocol | str,
) -> None:
    if source_protocol not in set(SourceProtocol) or destination_protocol not in set(DestinationProtocol):
        return
    if workload is WorkloadType.MAIL:
        if source_protocol not in {SourceProtocol.IMAP, SourceProtocol.POP3}:
            raise ValueError("Mail workload requires IMAP or POP3 source protocol.")
        if destination_protocol is not DestinationProtocol.IMAP:
            raise ValueError("Mail workload requires IMAP destination protocol.")
        return
    if workload is WorkloadType.CALENDAR:
        if source_protocol is not SourceProtocol.CALDAV:
            raise ValueError("Calendar workload requires CalDAV source protocol.")
        if destination_protocol is not DestinationProtocol.CALDAV:
            raise ValueError("Calendar workload requires CalDAV destination protocol.")
        return
    if workload is WorkloadType.TASKS:
        if source_protocol is not SourceProtocol.CALDAV:
            raise ValueError("Tasks workload requires CalDAV source protocol.")
        if destination_protocol is not DestinationProtocol.CALDAV:
            raise ValueError("Tasks workload requires CalDAV destination protocol.")
        return
    if workload is WorkloadType.CONTACTS:
        if source_protocol is not SourceProtocol.CARDDAV:
            raise ValueError("Contacts workload requires CardDAV source protocol.")
        if destination_protocol is not DestinationProtocol.CARDDAV:
            raise ValueError("Contacts workload requires CardDAV destination protocol.")
        return
    if workload is WorkloadType.NOTES:
        if source_protocol is not SourceProtocol.CALDAV:
            raise ValueError("Notes workload currently expects CalDAV source protocol.")
        if destination_protocol is not DestinationProtocol.CALDAV:
            raise ValueError(
                "Notes workload currently expects CalDAV destination protocol."
            )
        return


@dataclass(slots=True)
class SourceEndpoint:
    protocol: SourceProtocol | str
    connection: ConnectionConfig
    include_collections: list[str] | None = None
    provider_id: str | None = None

    @property
    def include_mailboxes(self) -> list[str] | None:
        return self.include_collections

    @include_mailboxes.setter
    def include_mailboxes(self, value: list[str] | None) -> None:
        self.include_collections = value

    @classmethod
    def from_dict(cls, payload: dict) -> "SourceEndpoint":
        raw = str(payload["protocol"]).lower()
        try:
            protocol: SourceProtocol | str = SourceProtocol(raw)
        except ValueError:
            protocol = raw
        if protocol is SourceProtocol.IMAP:
            default_port = 993
        elif protocol is SourceProtocol.POP3:
            default_port = 995
        else:
            default_port = 443
        connection = ConnectionConfig.from_dict(
            payload=payload["connection"],
            default_port=default_port,
        )
        include_collections = payload.get(
            "include_collections",
            payload.get("include_mailboxes"),
        )
        if include_collections is not None:
            include_collections = [
                str(collection_name) for collection_name in include_collections
            ]
        return cls(
            protocol=protocol,
            connection=connection,
            include_collections=include_collections,
            provider_id=str(payload["provider_id"]) if "provider_id" in payload else None,
        )

    def to_dict(self, *, redact_password: bool = False) -> dict:
        return {
            "protocol": str(self.protocol),
            "connection": self.connection.to_dict(redact_password=redact_password),
            "include_collections": self.include_collections,
            "include_mailboxes": self.include_collections,
            "provider_id": self.provider_id,
        }


@dataclass(slots=True)
class DestinationEndpoint:
    protocol: DestinationProtocol | str
    connection: ConnectionConfig
    root_collection: str = "Migrated"
    provider_id: str | None = None

    @property
    def root_mailbox(self) -> str:
        return self.root_collection

    @root_mailbox.setter
    def root_mailbox(self, value: str) -> None:
        self.root_collection = value

    @classmethod
    def from_dict(cls, payload: dict) -> "DestinationEndpoint":
        raw = str(payload["protocol"]).lower()
        try:
            protocol: DestinationProtocol | str = DestinationProtocol(raw)
        except ValueError:
            protocol = raw
        default_port = 993 if protocol is DestinationProtocol.IMAP else 443
        connection = ConnectionConfig.from_dict(
            payload=payload["connection"],
            default_port=default_port,
        )
        return cls(
            protocol=protocol,
            connection=connection,
            root_collection=str(
                payload.get("root_collection", payload.get("root_mailbox", "Migrated"))
            ),
            provider_id=str(payload["provider_id"]) if "provider_id" in payload else None,
        )

    def to_dict(self, *, redact_password: bool = False) -> dict:
        return {
            "protocol": str(self.protocol),
            "connection": self.connection.to_dict(redact_password=redact_password),
            "root_collection": self.root_collection,
            "root_mailbox": self.root_collection,
            "provider_id": self.provider_id,
        }


@dataclass(slots=True)
class MigrationOptions:
    sync_mode: SyncMode = SyncMode.FULL
    incremental_base_job_id: str | None = None
    dry_run: bool = False
    max_errors: int = 25
    pop3_destination_mailbox: str = "POP3-Inbox"
    max_retries: int = 0

    @classmethod
    def from_dict(cls, payload: dict | None) -> "MigrationOptions":
        payload = payload or {}
        sync_mode_raw = str(payload.get("sync_mode", SyncMode.FULL.value)).lower()
        try:
            sync_mode = SyncMode(sync_mode_raw)
        except ValueError:
            sync_mode = SyncMode.FULL

        incremental_base_job_id = payload.get("incremental_base_job_id")
        if incremental_base_job_id is not None:
            incremental_base_job_id = str(incremental_base_job_id).strip() or None
        return cls(
            sync_mode=sync_mode,
            incremental_base_job_id=incremental_base_job_id,
            dry_run=bool(payload.get("dry_run", False)),
            max_errors=max(int(payload.get("max_errors", 25)), 1),
            pop3_destination_mailbox=str(
                payload.get("pop3_destination_mailbox", "POP3-Inbox")
            ),
            max_retries=max(int(payload.get("max_retries", 0)), 0),
        )

    def to_dict(self) -> dict:
        return {
            "sync_mode": self.sync_mode.value,
            "incremental_base_job_id": self.incremental_base_job_id,
            "dry_run": self.dry_run,
            "max_errors": self.max_errors,
            "pop3_destination_mailbox": self.pop3_destination_mailbox,
            "max_retries": self.max_retries,
        }


@dataclass(slots=True)
class MigrationRequest:
    source: SourceEndpoint
    destination: DestinationEndpoint
    workload: WorkloadType = WorkloadType.MAIL
    folder_mapping: dict[str, str] = field(default_factory=dict)
    options: MigrationOptions = field(default_factory=MigrationOptions)
    job_name: str | None = None

    @classmethod
    def from_dict(cls, payload: dict) -> "MigrationRequest":
        folder_mapping_raw = payload.get(
            "folder_mapping",
            payload.get("collection_mapping", {}),
        )
        folder_mapping = {
            str(source_mailbox): str(destination_mailbox)
            for source_mailbox, destination_mailbox in folder_mapping_raw.items()
        }
        source = SourceEndpoint.from_dict(payload["source"])
        destination = DestinationEndpoint.from_dict(payload["destination"])
        workload_raw = payload.get("workload")
        if workload_raw is None:
            workload = _infer_workload(
                source_protocol=source.protocol,
                destination_protocol=destination.protocol,
            )
        else:
            workload = WorkloadType(str(workload_raw).strip().lower())
        _validate_workload_protocols(
            workload=workload,
            source_protocol=source.protocol,
            destination_protocol=destination.protocol,
        )
        return cls(
            source=source,
            destination=destination,
            workload=workload,
            folder_mapping=folder_mapping,
            options=MigrationOptions.from_dict(payload.get("options")),
            job_name=str(payload["job_name"]) if "job_name" in payload else None,
        )

    def to_dict(self, *, redact_password: bool = False) -> dict:
        return {
            "job_name": self.job_name,
            "source": self.source.to_dict(redact_password=redact_password),
            "destination": self.destination.to_dict(redact_password=redact_password),
            "workload": self.workload.value,
            "folder_mapping": self.folder_mapping,
            "collection_mapping": self.folder_mapping,
            "options": self.options.to_dict(),
        }


@dataclass(slots=True)
class CollectionSnapshot:
    name: str
    estimated_items: int

    @property
    def estimated_messages(self) -> int:
        return self.estimated_items


@dataclass(slots=True)
class MailboxSnapshot:
    name: str
    estimated_messages: int
    @property
    def estimated_items(self) -> int:
        return self.estimated_messages


@dataclass(slots=True)
class MigrationPlanItem:
    source_collection: str
    destination_collection: str
    estimated_items: int

    def __init__(
        self,
        source_collection: str | None = None,
        destination_collection: str | None = None,
        estimated_items: int | None = None,
        *,
        source_mailbox: str | None = None,
        destination_mailbox: str | None = None,
        estimated_messages: int | None = None,
    ) -> None:
        resolved_source_collection = (
            source_collection if source_collection is not None else source_mailbox
        )
        resolved_destination_collection = (
            destination_collection
            if destination_collection is not None
            else destination_mailbox
        )
        resolved_estimated_items = (
            estimated_items if estimated_items is not None else estimated_messages
        )
        if resolved_source_collection is None:
            raise TypeError(
                "Missing required argument: source_collection (or source_mailbox)."
            )
        if resolved_destination_collection is None:
            raise TypeError(
                "Missing required argument: destination_collection (or destination_mailbox)."
            )
        if resolved_estimated_items is None:
            raise TypeError(
                "Missing required argument: estimated_items (or estimated_messages)."
            )
        self.source_collection = str(resolved_source_collection)
        self.destination_collection = str(resolved_destination_collection)
        self.estimated_items = int(resolved_estimated_items)

    @property
    def source_mailbox(self) -> str:
        return self.source_collection

    @property
    def destination_mailbox(self) -> str:
        return self.destination_collection

    @property
    def estimated_messages(self) -> int:
        return self.estimated_items

    def to_dict(self) -> dict:
        return {
            "source_collection": self.source_collection,
            "destination_collection": self.destination_collection,
            "estimated_items": self.estimated_items,
            "source_mailbox": self.source_collection,
            "destination_mailbox": self.destination_collection,
            "estimated_messages": self.estimated_items,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "MigrationPlanItem":
        source_collection = payload.get("source_collection", payload.get("source_mailbox"))
        destination_collection = payload.get(
            "destination_collection",
            payload.get("destination_mailbox"),
        )
        estimated_items = payload.get("estimated_items", payload.get("estimated_messages"))
        if source_collection is None:
            raise ValueError("Missing required field: source_collection.")
        if destination_collection is None:
            raise ValueError("Missing required field: destination_collection.")
        if estimated_items is None:
            raise ValueError("Missing required field: estimated_items.")
        return cls(
            source_collection=str(source_collection),
            destination_collection=str(destination_collection),
            estimated_items=int(estimated_items),
        )


@dataclass(slots=True)
class MigrationPlan:
    items: list[MigrationPlanItem] = field(default_factory=list)
    @property
    def total_estimated_items(self) -> int:
        return sum(item.estimated_items for item in self.items)

    @property
    def total_estimated_messages(self) -> int:
        return self.total_estimated_items

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_estimated_items": self.total_estimated_items,
            "total_estimated_messages": self.total_estimated_messages,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "MigrationPlan":
        return cls(
            items=[MigrationPlanItem.from_dict(item) for item in payload.get("items", [])]
        )


@dataclass(slots=True)
class SourceMessage:
    source_mailbox: str
    source_id: str
    raw_message: bytes
    flags: set[str] = field(default_factory=set)
    internal_date: datetime | None = None
    message_id: str | None = None
    @property
    def source_collection(self) -> str:
        return self.source_mailbox


@dataclass(slots=True)
class SourceItem:
    source_collection: str
    source_id: str
    raw_payload: bytes
    content_type: str | None = None
    version_token: str | None = None
    item_key: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def source_mailbox(self) -> str:
        return self.source_collection

    @property
    def raw_message(self) -> bytes:
        return self.raw_payload



@dataclass(slots=True)
class MigrationReport:
    job_id: str
    status: JobStatus
    migrated_count: int
    skipped_count: int
    failed_count: int
    dry_run: bool
    started_at: datetime | None
    finished_at: datetime | None
    error_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "migrated_count": self.migrated_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error_messages": self.error_messages,
        }
