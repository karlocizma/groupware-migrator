from __future__ import annotations

from email import policy
from email.parser import BytesHeaderParser
import hashlib


def normalize_message_id(message_id: str | None) -> str | None:
    if not message_id:
        return None
    normalized = message_id.strip().lstrip("<").rstrip(">").strip().lower()
    return normalized or None


def extract_message_id(raw_message: bytes) -> str | None:
    try:
        parsed_headers = BytesHeaderParser(policy=policy.default).parsebytes(
            raw_message,
            headersonly=True,
        )
    except Exception:
        return None
    return normalize_message_id(parsed_headers.get("Message-ID"))


def build_message_fingerprint(
    raw_message: bytes,
    *,
    source_id: str | None = None,
    message_id: str | None = None,
) -> str:
    normalized_message_id = normalize_message_id(message_id) or extract_message_id(raw_message)
    if normalized_message_id:
        seed = f"mid:{normalized_message_id}|size:{len(raw_message)}"
    else:
        raw_digest = hashlib.sha256(raw_message).hexdigest()
        source_hint = source_id or ""
        seed = f"raw:{raw_digest}|source:{source_hint}|size:{len(raw_message)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def build_item_fingerprint(
    *,
    source_collection: str,
    source_id: str,
    raw_payload: bytes,
    version_token: str | None = None,
) -> str:
    payload_digest = hashlib.sha256(raw_payload).hexdigest()
    source_seed = f"{source_collection.strip().casefold()}::{source_id.strip()}"
    version_seed = version_token.strip() if version_token else ""
    seed = f"item:{source_seed}|version:{version_seed}|payload:{payload_digest}|size:{len(raw_payload)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()
