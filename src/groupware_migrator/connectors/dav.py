from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import ssl
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from groupware_migrator.connectors.auth import resolve_oauth_access_token
from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.models import (
    AuthMode,
    CollectionSnapshot,
    ConnectionConfig,
    DestinationProtocol,
    MailboxSnapshot,
    SourceItem,
    SourceMessage,
    SourceProtocol,
    TlsProfile,
)


logger = logging.getLogger(__name__)

_DAV_NS = "DAV:"
_CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
_CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"

_NAMESPACES = {
    "d": _DAV_NS,
    "c": _CALDAV_NS,
    "card": _CARDDAV_NS,
}


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


def _normalize_path(raw_path: str) -> str:
    value = raw_path.strip()
    if not value:
        return "/"
    if not value.startswith("/"):
        value = "/" + value
    return value


def _normalize_href(raw_href: str) -> str:
    parsed = urlparse(raw_href)
    if parsed.scheme and parsed.netloc:
        path = parsed.path
    else:
        path = raw_href
    return _normalize_path(unquote(path))


def _safe_text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _build_propfind_body() -> bytes:
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        "<d:propfind xmlns:d=\"DAV:\" xmlns:c=\"urn:ietf:params:xml:ns:caldav\" "
        "xmlns:card=\"urn:ietf:params:xml:ns:carddav\">"
        "<d:prop>"
        "<d:displayname/>"
        "<d:resourcetype/>"
        "<d:getetag/>"
        "<d:getcontenttype/>"
        "</d:prop>"
        "</d:propfind>"
    ).encode("utf-8")


@dataclass(slots=True)
class _DavResponse:
    href: str
    resource_types: set[str]
    display_name: str
    etag: str | None
    content_type: str | None

    @property
    def is_collection(self) -> bool:
        return "collection" in self.resource_types


class _DavConnectorBase:
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self._ssl_context = _build_ssl_context(config)
        self._base_url, self._base_path = self._build_base_url()

    def _build_base_url(self) -> tuple[str, str]:
        host_raw = self.config.host.strip()
        if not host_raw:
            raise ValueError("DAV host must not be empty.")

        if host_raw.startswith("http://") or host_raw.startswith("https://"):
            parsed = urlparse(host_raw)
            scheme = parsed.scheme
            netloc = parsed.netloc
            path = parsed.path or "/"
        else:
            scheme = "https" if self.config.use_ssl else "http"
            if "/" in host_raw:
                host_only, _, path_suffix = host_raw.partition("/")
                netloc = f"{host_only}:{self.config.port}"
                path = "/" + path_suffix
            else:
                netloc = f"{host_raw}:{self.config.port}"
                path = "/"

        normalized_path = _normalize_path(path)
        if not normalized_path.endswith("/"):
            normalized_path += "/"
        return f"{scheme}://{netloc}{normalized_path}", normalized_path

    def _auth_headers(self) -> dict[str, str]:
        if self.config.auth_mode is AuthMode.OAUTH2:
            access_token = resolve_oauth_access_token(self.config)
            return {"Authorization": f"Bearer {access_token}"}

        if not self.config.password:
            raise ValueError(
                "Password authentication selected but no password is configured."
            )
        raw = f"{self.config.username}:{self.config.password}".encode("utf-8")
        import base64

        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        expected_statuses = expected_statuses or {200, 201, 204, 207}
        target_url = path if path.startswith("http://") or path.startswith("https://") else urljoin(self._base_url, path.lstrip("/"))

        request_headers = {
            "User-Agent": "groupware-migrator/0.1",
            "Accept": "*/*",
            **self._auth_headers(),
        }
        if headers:
            request_headers.update(headers)
        if data is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/xml; charset=utf-8"

        request = Request(
            target_url,
            data=data,
            method=method.upper(),
            headers=request_headers,
        )
        try:
            with urlopen(
                request,
                timeout=self.config.timeout_seconds,
                context=self._ssl_context,
            ) as response:
                status = int(getattr(response, "status", response.getcode()))
                body = response.read()
                response_headers = {key: value for key, value in response.headers.items()}
        except HTTPError as exc:
            status = int(exc.code)
            body = exc.read()
            response_headers = {key: value for key, value in exc.headers.items()}
            if status not in expected_statuses:
                raise RuntimeError(f"DAV {method} request failed ({status}): {body[:400]!r}") from exc
            return status, response_headers, body

        if status not in expected_statuses:
            raise RuntimeError(f"DAV {method} request failed ({status}): {body[:400]!r}")
        return status, response_headers, body

    def _propfind(self, path: str, *, depth: int = 1) -> list[_DavResponse]:
        _, _, body = self._request(
            "PROPFIND",
            path,
            data=_build_propfind_body(),
            headers={
                "Depth": str(depth),
                "Content-Type": "application/xml; charset=utf-8",
            },
            expected_statuses={207},
        )
        root = ET.fromstring(body or b"<d:multistatus xmlns:d='DAV:'/>")
        responses: list[_DavResponse] = []
        for response_node in root.findall("d:response", _NAMESPACES):
            href = _safe_text(response_node.find("d:href", _NAMESPACES))
            href_path = _normalize_href(href)
            prop = response_node.find("d:propstat/d:prop", _NAMESPACES)
            if prop is None:
                continue
            resource_type_node = prop.find("d:resourcetype", _NAMESPACES)
            resource_types: set[str] = set()
            if resource_type_node is not None:
                for child in resource_type_node:
                    local_name = child.tag.split("}")[-1].lower()
                    if local_name:
                        resource_types.add(local_name)
            display_name = _safe_text(prop.find("d:displayname", _NAMESPACES))
            etag_raw = _safe_text(prop.find("d:getetag", _NAMESPACES))
            content_type_raw = _safe_text(prop.find("d:getcontenttype", _NAMESPACES))
            responses.append(
                _DavResponse(
                    href=href_path,
                    resource_types=resource_types,
                    display_name=display_name,
                    etag=etag_raw or None,
                    content_type=content_type_raw or None,
                )
            )
        return responses

    def _relative_to_base(self, path: str) -> str:
        normalized = _normalize_path(path)
        if normalized.startswith(self._base_path):
            relative = normalized[len(self._base_path) :]
            return relative.strip("/")
        return normalized.strip("/")

    def _collection_path(self, collection: str) -> str:
        normalized = _normalize_path(collection.strip("/"))
        if normalized.startswith(self._base_path):
            path = normalized
        else:
            path = _normalize_path(self._base_path.strip("/") + "/" + collection.strip("/"))
        if not path.endswith("/"):
            path += "/"
        return path

    def _list_collection_items(self, collection: str) -> list[_DavResponse]:
        collection_path = self._collection_path(collection)
        responses = self._propfind(collection_path, depth=1)
        items: list[_DavResponse] = []
        for response in responses:
            if response.is_collection:
                continue
            if _normalize_path(response.href) == collection_path.rstrip("/"):
                continue
            items.append(response)
        return items

    def validate(self) -> None:
        self._propfind(self._base_path, depth=0)
        logger.debug("DAV connection validated: %s@%s", self.config.username, self.config.host)


class _DavSourceConnector(_DavConnectorBase, SourceConnector):
    protocol: SourceProtocol
    _collection_type_marker: str
    _default_content_type: str

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        return [
            MailboxSnapshot(
                name=snapshot.name,
                estimated_messages=int(snapshot.estimated_items),
            )
            for snapshot in self.list_collections()
        ]

    def list_collections(self) -> list[CollectionSnapshot]:
        snapshots: list[CollectionSnapshot] = []
        for response in self._propfind(self._base_path, depth=1):
            if not response.is_collection:
                continue
            if self._collection_type_marker not in response.resource_types:
                continue
            collection_name = self._relative_to_base(response.href)
            if not collection_name:
                continue
            estimated_items = len(self._list_collection_items(collection_name))
            snapshots.append(
                CollectionSnapshot(
                    name=collection_name,
                    estimated_items=estimated_items,
                )
            )
        return snapshots

    def iter_messages(
        self,
        mailbox: str,
        resume_from: str | None = None,
    ) -> Iterable[SourceMessage]:
        raise ValueError(
            f"{self.protocol.value} source does not expose RFC822 message iteration."
        )

    def estimate_pending_items(
        self,
        collection: str,
        resume_from: str | None,
    ) -> int | None:
        if resume_from is None:
            return None
        item_ids = sorted(
            self._relative_to_base(item.href)
            for item in self._list_collection_items(collection)
        )
        return sum(1 for item_id in item_ids if item_id > resume_from)

    def iter_items(
        self,
        collection: str,
        resume_from: str | None = None,
    ) -> Iterable[SourceItem]:
        item_entries = sorted(
            self._list_collection_items(collection),
            key=lambda entry: self._relative_to_base(entry.href),
        )
        for entry in item_entries:
            source_id = self._relative_to_base(entry.href)
            if resume_from and source_id <= resume_from:
                continue
            _, item_headers, item_body = self._request(
                "GET",
                entry.href,
                expected_statuses={200},
            )
            content_type = (
                item_headers.get("Content-Type")
                or entry.content_type
                or self._default_content_type
            )
            item_key = source_id.rsplit("/", 1)[-1] if "/" in source_id else source_id
            yield SourceItem(
                source_collection=collection,
                source_id=source_id,
                raw_payload=item_body,
                content_type=content_type,
                version_token=entry.etag,
                item_key=item_key,
                metadata={
                    "etag": entry.etag or "",
                    "content_type": content_type,
                    "item_name": item_key,
                    "source_href": entry.href,
                },
            )


class _DavDestinationConnector(_DavConnectorBase, DestinationConnector):
    protocol: DestinationProtocol
    _default_content_type: str
    _default_extension: str

    def ensure_mailbox(self, mailbox: str) -> None:
        self.ensure_collection(mailbox)

    def ensure_collection(self, collection: str) -> None:
        collection_path = self._collection_path(collection)
        existing_paths = {_normalize_path(response.href) for response in self._propfind(collection_path, depth=0)}
        if collection_path in existing_paths or collection_path.rstrip("/") in existing_paths:
            return

        path_chunks = [chunk for chunk in collection_path.strip("/").split("/") if chunk]
        current = ""
        for chunk in path_chunks:
            current = f"{current}/{chunk}" if current else f"/{chunk}"
            current_path = _normalize_path(current)
            if not current_path.endswith("/"):
                current_path += "/"
            try:
                self._request("MKCOL", current_path, data=b"", expected_statuses={201, 405})
            except RuntimeError:
                existing = {_normalize_path(response.href) for response in self._propfind(current_path, depth=0)}
                if current_path not in existing and current_path.rstrip("/") not in existing:
                    raise

    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ) -> str | None:
        _ = flags, internal_date
        raise ValueError(
            f"{self.protocol.value} destination does not support RFC822 message append."
        )

    def _destination_item_path(
        self,
        collection: str,
        source_id: str,
        *,
        metadata: dict[str, str],
    ) -> str:
        item_name = metadata.get("item_name", "").strip()
        if not item_name:
            source_tail = source_id.rsplit("/", 1)[-1].strip()
            item_name = source_tail or hashlib.sha256(source_id.encode("utf-8")).hexdigest()
        if "." not in item_name:
            item_name = f"{item_name}{self._default_extension}"
        collection_path = self._collection_path(collection)
        return f"{collection_path}{item_name}"

    def upsert_item(
        self,
        collection: str,
        source_id: str,
        raw_payload: bytes,
        *,
        metadata: dict[str, str] | None = None,
    ) -> str | None:
        metadata = metadata or {}
        self.ensure_collection(collection)
        destination_path = self._destination_item_path(
            collection,
            source_id,
            metadata=metadata,
        )
        content_type = metadata.get("content_type") or self._default_content_type
        _, _, _ = self._request(
            "PUT",
            destination_path,
            data=raw_payload,
            headers={
                "Content-Type": content_type,
            },
            expected_statuses={200, 201, 204},
        )
        return self._relative_to_base(destination_path)


class CalDavSourceConnector(_DavSourceConnector):
    protocol = SourceProtocol.CALDAV
    _collection_type_marker = "calendar"
    _default_content_type = "text/calendar; charset=utf-8"


class CardDavSourceConnector(_DavSourceConnector):
    protocol = SourceProtocol.CARDDAV
    _collection_type_marker = "addressbook"
    _default_content_type = "text/vcard; charset=utf-8"


class CalDavDestinationConnector(_DavDestinationConnector):
    protocol = DestinationProtocol.CALDAV
    _default_content_type = "text/calendar; charset=utf-8"
    _default_extension = ".ics"


class CardDavDestinationConnector(_DavDestinationConnector):
    protocol = DestinationProtocol.CARDDAV
    _default_content_type = "text/vcard; charset=utf-8"
    _default_extension = ".vcf"
