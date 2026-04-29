from __future__ import annotations

import csv
from dataclasses import dataclass
from copy import deepcopy
import io
from typing import Any

from groupware_migrator.models import MigrationRequest


_TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


@dataclass(slots=True)
class BatchCsvRow:
    row_number: int
    source_username: str
    destination_username: str
    job_name: str | None
    request: MigrationRequest | None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.request is not None and not self.error

    def to_preview_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "source_username": self.source_username,
            "destination_username": self.destination_username,
            "job_name": self.job_name,
            "valid": self.valid,
            "error": self.error,
        }


def _normalized_row(raw_row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw_row.items():
        if key is None:
            continue
        normalized_key = str(key).strip().lower()
        normalized_value = "" if value is None else str(value).strip()
        normalized[normalized_key] = normalized_value
    return normalized


def _row_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        value = value.strip()
        if value:
            return value
    return ""


def _parse_bool(raw_value: str, *, fallback: bool) -> bool:
    value = raw_value.strip().lower()
    if not value:
        return fallback
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean value '{raw_value}'.")


def _parse_int(raw_value: str, *, fallback: int) -> int:
    value = raw_value.strip()
    if not value:
        return fallback
    return int(value)


def _parse_mailbox_list(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    normalized = raw_value.replace("|", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _parse_folder_mapping(raw_value: str) -> dict[str, str]:
    if not raw_value.strip():
        return {}
    mapping: dict[str, str] = {}
    normalized = raw_value.replace("|", ";")
    for chunk in normalized.split(";"):
        pair = chunk.strip()
        if not pair:
            continue
        separator = "=>" if "=>" in pair else "="
        source_dest = [part.strip() for part in pair.split(separator, 1)]
        if len(source_dest) != 2 or not source_dest[0] or not source_dest[1]:
            raise ValueError(f"Invalid folder mapping pair '{pair}'.")
        mapping[source_dest[0]] = source_dest[1]
    return mapping


def _apply_row_overrides(
    *,
    base_payload: dict[str, Any],
    row: dict[str, str],
    row_number: int,
) -> dict[str, Any]:
    payload = deepcopy(base_payload)

    source_payload = payload.setdefault("source", {})
    source_connection = source_payload.setdefault("connection", {})
    destination_payload = payload.setdefault("destination", {})
    destination_payload.setdefault("protocol", "imap")
    destination_connection = destination_payload.setdefault("connection", {})
    options_payload = payload.setdefault("options", {})

    workload = _row_value(row, "workload")
    if workload:
        payload["workload"] = workload.lower()

    source_protocol = _row_value(row, "source_protocol")
    if source_protocol:
        source_payload["protocol"] = source_protocol.lower()
    destination_protocol = _row_value(row, "destination_protocol")
    if destination_protocol:
        destination_payload["protocol"] = destination_protocol.lower()

    source_provider_id = _row_value(row, "source_provider_id")
    if source_provider_id:
        source_payload["provider_id"] = source_provider_id

    destination_provider_id = _row_value(row, "destination_provider_id")
    if destination_provider_id:
        destination_payload["provider_id"] = destination_provider_id

    source_host = _row_value(row, "source_host")
    if source_host:
        source_connection["host"] = source_host
    source_port = _row_value(row, "source_port")
    if source_port:
        source_connection["port"] = int(source_port)
    source_username = _row_value(row, "source_username")
    if source_username:
        source_connection["username"] = source_username
    source_password = _row_value(row, "source_password")
    if source_password:
        source_connection["password"] = source_password
    source_use_ssl = _row_value(row, "source_use_ssl")
    if source_use_ssl:
        source_connection["use_ssl"] = _parse_bool(
            source_use_ssl,
            fallback=bool(source_connection.get("use_ssl", True)),
        )
    source_tls_profile = _row_value(row, "source_tls_profile")
    if source_tls_profile:
        source_connection["tls_profile"] = source_tls_profile.lower()
    source_auth_mode = _row_value(row, "source_auth_mode")
    if source_auth_mode:
        source_connection["auth_mode"] = source_auth_mode.lower()
    source_oauth_access_token = _row_value(row, "source_oauth_access_token")
    if source_oauth_access_token:
        source_connection["oauth_access_token"] = source_oauth_access_token
    source_oauth_refresh_token = _row_value(row, "source_oauth_refresh_token")
    if source_oauth_refresh_token:
        source_connection["oauth_refresh_token"] = source_oauth_refresh_token
    source_oauth_client_id = _row_value(row, "source_oauth_client_id")
    if source_oauth_client_id:
        source_connection["oauth_client_id"] = source_oauth_client_id
    source_oauth_client_secret = _row_value(row, "source_oauth_client_secret")
    if source_oauth_client_secret:
        source_connection["oauth_client_secret"] = source_oauth_client_secret
    source_oauth_token_url = _row_value(row, "source_oauth_token_url")
    if source_oauth_token_url:
        source_connection["oauth_token_url"] = source_oauth_token_url
    source_oauth_scope = _row_value(row, "source_oauth_scope")
    if source_oauth_scope:
        source_connection["oauth_scope"] = source_oauth_scope

    include_collections_raw = _row_value(
        row,
        "source_include_collections",
        "source_include_mailboxes",
    )
    if include_collections_raw:
        include_collections = _parse_mailbox_list(include_collections_raw)
        source_payload["include_collections"] = include_collections
        source_payload["include_mailboxes"] = include_collections

    destination_host = _row_value(row, "destination_host")
    if destination_host:
        destination_connection["host"] = destination_host
    destination_port = _row_value(row, "destination_port")
    if destination_port:
        destination_connection["port"] = int(destination_port)
    destination_username = _row_value(row, "destination_username")
    if destination_username:
        destination_connection["username"] = destination_username
    destination_password = _row_value(row, "destination_password")
    if destination_password:
        destination_connection["password"] = destination_password
    destination_use_ssl = _row_value(row, "destination_use_ssl")
    if destination_use_ssl:
        destination_connection["use_ssl"] = _parse_bool(
            destination_use_ssl,
            fallback=bool(destination_connection.get("use_ssl", True)),
        )
    destination_tls_profile = _row_value(row, "destination_tls_profile")
    if destination_tls_profile:
        destination_connection["tls_profile"] = destination_tls_profile.lower()
    destination_auth_mode = _row_value(row, "destination_auth_mode")
    if destination_auth_mode:
        destination_connection["auth_mode"] = destination_auth_mode.lower()
    destination_oauth_access_token = _row_value(row, "destination_oauth_access_token")
    if destination_oauth_access_token:
        destination_connection["oauth_access_token"] = destination_oauth_access_token
    destination_oauth_refresh_token = _row_value(row, "destination_oauth_refresh_token")
    if destination_oauth_refresh_token:
        destination_connection["oauth_refresh_token"] = destination_oauth_refresh_token
    destination_oauth_client_id = _row_value(row, "destination_oauth_client_id")
    if destination_oauth_client_id:
        destination_connection["oauth_client_id"] = destination_oauth_client_id
    destination_oauth_client_secret = _row_value(row, "destination_oauth_client_secret")
    if destination_oauth_client_secret:
        destination_connection["oauth_client_secret"] = destination_oauth_client_secret
    destination_oauth_token_url = _row_value(row, "destination_oauth_token_url")
    if destination_oauth_token_url:
        destination_connection["oauth_token_url"] = destination_oauth_token_url
    destination_oauth_scope = _row_value(row, "destination_oauth_scope")
    if destination_oauth_scope:
        destination_connection["oauth_scope"] = destination_oauth_scope

    destination_root_collection = _row_value(
        row,
        "destination_root_collection",
        "destination_root_mailbox",
    )
    if destination_root_collection:
        destination_payload["root_collection"] = destination_root_collection
        destination_payload["root_mailbox"] = destination_root_collection

    collection_mapping_raw = _row_value(row, "collection_mapping", "folder_mapping")
    if collection_mapping_raw:
        mapping = _parse_folder_mapping(collection_mapping_raw)
        payload["folder_mapping"] = mapping
        payload["collection_mapping"] = mapping

    pop3_destination_mailbox = _row_value(row, "pop3_destination_mailbox")
    if pop3_destination_mailbox:
        options_payload["pop3_destination_mailbox"] = pop3_destination_mailbox
    sync_mode = _row_value(row, "sync_mode")
    if sync_mode:
        options_payload["sync_mode"] = sync_mode.lower()
    incremental_base_job_id = _row_value(row, "incremental_base_job_id")
    if incremental_base_job_id:
        options_payload["incremental_base_job_id"] = incremental_base_job_id

    dry_run_raw = _row_value(row, "dry_run")
    if dry_run_raw:
        options_payload["dry_run"] = _parse_bool(
            dry_run_raw,
            fallback=bool(options_payload.get("dry_run", False)),
        )

    max_errors_raw = _row_value(row, "max_errors")
    if max_errors_raw:
        options_payload["max_errors"] = _parse_int(
            max_errors_raw,
            fallback=int(options_payload.get("max_errors", 25)),
        )

    job_name = _row_value(row, "job_name")
    if job_name:
        payload["job_name"] = job_name
    elif not payload.get("job_name"):
        source_hint = str(source_connection.get("username", "")).strip() or "source"
        payload["job_name"] = f"batch-row-{row_number}-{source_hint}"

    return payload


def build_batch_rows(
    csv_content: str,
    *,
    base_request_payload: dict[str, Any] | None = None,
) -> list[BatchCsvRow]:
    if not csv_content.strip():
        raise ValueError("CSV content is empty.")

    reader = csv.DictReader(io.StringIO(csv_content))
    if not reader.fieldnames:
        raise ValueError("CSV header row is required.")

    base_request_payload = deepcopy(base_request_payload or {})
    rows: list[BatchCsvRow] = []

    for row_number, raw_row in enumerate(reader, start=2):
        normalized_row = _normalized_row(raw_row)
        if not any(normalized_row.values()):
            continue

        try:
            payload = _apply_row_overrides(
                base_payload=base_request_payload,
                row=normalized_row,
                row_number=row_number,
            )
            request = MigrationRequest.from_dict(payload)
            rows.append(
                BatchCsvRow(
                    row_number=row_number,
                    source_username=request.source.connection.username,
                    destination_username=request.destination.connection.username,
                    job_name=request.job_name,
                    request=request,
                )
            )
        except Exception as exc:
            rows.append(
                BatchCsvRow(
                    row_number=row_number,
                    source_username=_row_value(normalized_row, "source_username"),
                    destination_username=_row_value(normalized_row, "destination_username"),
                    job_name=_row_value(normalized_row, "job_name") or None,
                    request=None,
                    error=str(exc),
                )
            )

    if not rows:
        raise ValueError("CSV contains no data rows.")
    return rows


def build_batch_preview(rows: list[BatchCsvRow]) -> dict[str, Any]:
    valid_rows = sum(1 for row in rows if row.valid)
    return {
        "total_rows": len(rows),
        "valid_rows": valid_rows,
        "invalid_rows": len(rows) - valid_rows,
        "items": [row.to_preview_dict() for row in rows],
    }
