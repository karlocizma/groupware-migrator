"""Webhook delivery: fire signed HTTP POST notifications on job events."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from groupware_migrator.engine.state import SQLiteStateStore

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_DELAYS = (0, 5, 15)  # seconds before each attempt (0 = immediate first try)
_TIMEOUT_SECONDS = 10


def _sign_payload(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookDeliveryManager:
    def __init__(self, state_store: "SQLiteStateStore"):
        self._state_store = state_store

    def fire(self, *, event_type: str, payload: dict, user_id: str | None = None) -> None:
        """Fire webhooks matching this event in a daemon thread (non-blocking)."""
        hooks = self._state_store.list_webhooks_for_event(event_type=event_type, user_id=user_id)
        if not hooks:
            return
        t = threading.Thread(
            target=self._deliver_all,
            args=(hooks, event_type, payload),
            daemon=True,
        )
        t.start()

    def _deliver_all(self, hooks: list[dict], event_type: str, payload: dict) -> None:
        for hook in hooks:
            self._deliver_one(hook, event_type, payload)

    def _deliver_one(self, hook: dict, event_type: str, payload: dict) -> None:
        url = hook["url"]
        secret = hook["secret"]
        webhook_id = hook["id"]

        envelope = {
            "event": event_type,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        body = json.dumps(envelope, sort_keys=True).encode()
        signature = _sign_payload(body, secret)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            delay = _RETRY_DELAYS[attempt - 1]
            if delay:
                time.sleep(delay)
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Groupware-Signature": signature,
                        "X-Groupware-Event": event_type,
                        "User-Agent": "GroupwareMigrator/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
                error = str(exc)
            except Exception as exc:
                status = 0
                error = str(exc)
                logger.warning("Webhook %s delivery attempt %d/%d failed: %s", webhook_id, attempt, _MAX_ATTEMPTS, error)
                self._state_store.append_webhook_delivery(
                    webhook_id=webhook_id,
                    event_type=event_type,
                    payload_json=body.decode(),
                    response_status=status,
                    error=error,
                    attempt=attempt,
                )
                if attempt < _MAX_ATTEMPTS:
                    continue
                return
            else:
                error = None

            self._state_store.append_webhook_delivery(
                webhook_id=webhook_id,
                event_type=event_type,
                payload_json=body.decode(),
                response_status=status,
                error=error,
                attempt=attempt,
            )
            if 200 <= status < 300:
                logger.debug("Webhook %s delivered (status %d)", webhook_id, status)
                return
            if status < 500:
                logger.warning("Webhook %s rejected (status %d); not retrying.", webhook_id, status)
                return
            # 5xx: retry
            logger.warning("Webhook %s got %d; retrying (%d/%d).", webhook_id, status, attempt, _MAX_ATTEMPTS)
