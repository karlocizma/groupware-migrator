# Email Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users receive HTML emails when migration jobs complete, fail, or are cancelled, with per-user opt-in toggles and system-wide SMTP configuration via env vars.

**Architecture:** A new `MailDeliveryManager` in `engine/mailer.py` uses stdlib `smtplib` to send emails in daemon threads (non-blocking, mirroring the existing `WebhookDeliveryManager` pattern). Preferences are stored in a new `notification_prefs` SQLite table. The manager is wired into `BackgroundJobManager._on_done()` alongside the existing webhook firing.

**Tech Stack:** Python stdlib `smtplib`, `email.mime.*`; SQLite (existing); FastAPI (existing); vanilla JS (existing).

---

## File Map

| File | Change |
|---|---|
| `src/groupware_migrator/engine/mailer.py` | **Create** — `MailDeliveryManager`, HTML/text templates, SMTP send helpers |
| `src/groupware_migrator/engine/state.py` | **Modify** — add `notification_prefs` table to schema, add `get_notification_prefs` and `set_notification_prefs` methods |
| `src/groupware_migrator/engine/background.py` | **Modify** — add `mail_manager` param, fire email in `_on_done()` |
| `src/groupware_migrator/api/routers/auth_router.py` | **Modify** — add `GET /auth/notifications` and `PATCH /auth/notifications` |
| `src/groupware_migrator/api/routers/admin_router.py` | **Modify** — add `mail_manager` param, add `POST /admin/smtp/test` |
| `src/groupware_migrator/api/app.py` | **Modify** — instantiate `MailDeliveryManager`, pass to `BackgroundJobManager` and `create_admin_router` |
| `src/groupware_migrator/api/static/scheduler.html` | **Modify** — add "Email Notifications" section with 3 toggles |
| `src/groupware_migrator/api/static/js/scheduler.js` | **Modify** — add `loadNotificationPrefs`, `saveNotificationPref` functions |
| `tests/test_email_notifications.py` | **Create** — all tests for this feature |

---

### Task 1: State store — `notification_prefs` table and methods

**Files:**
- Modify: `src/groupware_migrator/engine/state.py`
- Create: `tests/test_email_notifications.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_notifications.py`:

```python
"""Tests for email notification preferences in the state store."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _store(tmp: str) -> SQLiteStateStore:
    return SQLiteStateStore(Path(tmp) / "state.db")


def _user(store: SQLiteStateStore) -> str:
    return store.create_user(
        email="user@example.com",
        password_hash=hash_password("password"),
    )


def _make_request():
    from groupware_migrator.models import MigrationRequest
    from groupware_migrator.models.domain import (
        ConnectionConfig, DestinationEndpoint, DestinationProtocol,
        MigrationOptions, SourceEndpoint, SourceProtocol, WorkloadType,
    )
    return MigrationRequest(
        source=SourceEndpoint(
            protocol=SourceProtocol.IMAP,
            connection=ConnectionConfig(host="src", port=993, username="u", password="p"),
        ),
        destination=DestinationEndpoint(
            protocol=DestinationProtocol.IMAP,
            connection=ConnectionConfig(host="dst", port=993, username="u", password="p"),
        ),
        workload=WorkloadType.MAIL,
        options=MigrationOptions(),
    )


class TestNotificationPrefs(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = _store(self._tmp.name)
        self.user_id = _user(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_all_false(self):
        prefs = self.store.get_notification_prefs(self.user_id)
        self.assertFalse(prefs["on_completed"])
        self.assertFalse(prefs["on_failed"])
        self.assertFalse(prefs["on_cancelled"])

    def test_set_and_get(self):
        self.store.set_notification_prefs(
            self.user_id,
            on_completed=True,
            on_failed=True,
            on_cancelled=False,
        )
        prefs = self.store.get_notification_prefs(self.user_id)
        self.assertTrue(prefs["on_completed"])
        self.assertTrue(prefs["on_failed"])
        self.assertFalse(prefs["on_cancelled"])

    def test_upsert_updates_existing(self):
        self.store.set_notification_prefs(
            self.user_id,
            on_completed=True,
            on_failed=False,
            on_cancelled=False,
        )
        self.store.set_notification_prefs(
            self.user_id,
            on_completed=False,
            on_failed=True,
            on_cancelled=True,
        )
        prefs = self.store.get_notification_prefs(self.user_id)
        self.assertFalse(prefs["on_completed"])
        self.assertTrue(prefs["on_failed"])
        self.assertTrue(prefs["on_cancelled"])

    def test_unknown_user_returns_defaults(self):
        prefs = self.store.get_notification_prefs("nonexistent-id")
        self.assertFalse(prefs["on_completed"])
        self.assertFalse(prefs["on_failed"])
        self.assertFalse(prefs["on_cancelled"])
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.py -v
```

Expected: `AttributeError: 'SQLiteStateStore' object has no attribute 'get_notification_prefs'`

- [ ] **Step 3: Add `notification_prefs` table to the schema**

In `src/groupware_migrator/engine/state.py`, find the `CREATE TABLE IF NOT EXISTS org_memberships` block (around line 269) and add the new table immediately after the closing `);` of `org_memberships`, before the closing `"""` of the schema string:

```python
                CREATE TABLE IF NOT EXISTS notification_prefs (
                    user_id      TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    on_completed INTEGER NOT NULL DEFAULT 0,
                    on_failed    INTEGER NOT NULL DEFAULT 0,
                    on_cancelled INTEGER NOT NULL DEFAULT 0
                );
```

- [ ] **Step 4: Add `get_notification_prefs` and `set_notification_prefs` methods**

Add these two methods to `SQLiteStateStore` in `state.py`, after the `change_password` method:

```python
def get_notification_prefs(self, user_id: str) -> dict[str, bool]:
    with self._lock, self._connection() as connection:
        cursor = connection.execute(
            "SELECT on_completed, on_failed, on_cancelled FROM notification_prefs WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return {"on_completed": False, "on_failed": False, "on_cancelled": False}
    return {
        "on_completed": bool(row["on_completed"]),
        "on_failed": bool(row["on_failed"]),
        "on_cancelled": bool(row["on_cancelled"]),
    }

def set_notification_prefs(
    self,
    user_id: str,
    *,
    on_completed: bool,
    on_failed: bool,
    on_cancelled: bool,
) -> None:
    with self._lock, self._connection() as connection:
        connection.execute(
            """
            INSERT INTO notification_prefs (user_id, on_completed, on_failed, on_cancelled)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                on_completed = excluded.on_completed,
                on_failed    = excluded.on_failed,
                on_cancelled = excluded.on_cancelled
            """,
            (user_id, 1 if on_completed else 0, 1 if on_failed else 0, 1 if on_cancelled else 0),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.py -v
```

Expected: `4 tests … OK`

- [ ] **Step 6: Commit**

```bash
git add src/groupware_migrator/engine/state.py tests/test_email_notifications.py
git commit -m "feat: add notification_prefs table and state store methods"
```

---

### Task 2: `engine/mailer.py` — `MailDeliveryManager`

**Files:**
- Create: `src/groupware_migrator/engine/mailer.py`
- Modify: `tests/test_email_notifications.py`

- [ ] **Step 1: Write failing tests for `MailDeliveryManager`**

Append to `tests/test_email_notifications.py`:

```python
import os
import smtplib
from unittest.mock import MagicMock, call, patch

from groupware_migrator.engine.mailer import MailDeliveryManager


class TestMailDeliveryManagerIsConfigured(unittest.TestCase):
    def test_not_configured_when_no_host(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            with patch.dict(os.environ, {}, clear=True):
                # Remove SMTP_HOST if set
                os.environ.pop("SMTP_HOST", None)
                self.assertFalse(mgr.is_configured())

    def test_configured_when_host_set(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
                self.assertTrue(mgr.is_configured())


class TestMailDeliveryManagerFire(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = _store(self._tmp.name)
        self.user_id = _user(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_noop_when_not_configured(self):
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SMTP_HOST", None)
            with patch("threading.Thread") as mock_thread:
                mgr.fire(event_type="job.completed", job_row={"status": "completed"}, user_id=self.user_id)
                mock_thread.assert_not_called()

    def test_noop_when_user_id_is_none(self):
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("threading.Thread") as mock_thread:
                mgr.fire(event_type="job.completed", job_row={"status": "completed"}, user_id=None)
                mock_thread.assert_not_called()

    def test_noop_when_toggle_off(self):
        self.store.set_notification_prefs(
            self.user_id, on_completed=False, on_failed=False, on_cancelled=False
        )
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("threading.Thread") as mock_thread:
                mgr.fire(event_type="job.completed", job_row={"status": "completed"}, user_id=self.user_id)
                mock_thread.assert_not_called()

    def test_fires_thread_when_toggle_on(self):
        self.store.set_notification_prefs(
            self.user_id, on_completed=True, on_failed=False, on_cancelled=False
        )
        mgr = MailDeliveryManager(self.store)
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("threading.Thread") as mock_thread:
                mock_thread.return_value = MagicMock()
                mgr.fire(
                    event_type="job.completed",
                    job_row={"status": "completed", "job_name": "test"},
                    user_id=self.user_id,
                )
                mock_thread.assert_called_once()
                _, kwargs = mock_thread.call_args
                self.assertTrue(kwargs.get("daemon"))


class TestMailDeliveryManagerSendTest(unittest.TestCase):
    def test_send_test_starttls(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            env = {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "user@example.com",
                "SMTP_PASSWORD": "secret",
                "SMTP_TLS": "starttls",
            }
            with patch.dict(os.environ, env):
                with patch("smtplib.SMTP") as mock_smtp_cls:
                    mock_conn = MagicMock()
                    mock_smtp_cls.return_value.__enter__ = lambda s: mock_conn
                    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
                    mgr.send_test(to_address="admin@example.com")
                    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)

    def test_send_test_ssl(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            env = {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "465",
                "SMTP_TLS": "ssl",
            }
            with patch.dict(os.environ, env):
                with patch("smtplib.SMTP_SSL") as mock_ssl_cls:
                    mock_conn = MagicMock()
                    mock_ssl_cls.return_value.__enter__ = lambda s: mock_conn
                    mock_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)
                    mgr.send_test(to_address="admin@example.com")
                    mock_ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=10)

    def test_send_test_raises_on_smtp_error(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            mgr = MailDeliveryManager(store)
            with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
                with patch("smtplib.SMTP") as mock_smtp_cls:
                    mock_smtp_cls.side_effect = ConnectionRefusedError("refused")
                    with self.assertRaises(ConnectionRefusedError):
                        mgr.send_test(to_address="admin@example.com")
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.py -v
```

Expected: `ModuleNotFoundError: No module named 'groupware_migrator.engine.mailer'`

- [ ] **Step 3: Create `src/groupware_migrator/engine/mailer.py`**

```python
from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from groupware_migrator.engine.state import SQLiteStateStore

logger = logging.getLogger(__name__)

_EVENT_TOGGLE: dict[str, str] = {
    "job.completed": "on_completed",
    "job.failed": "on_failed",
    "job.cancelled": "on_cancelled",
}

_STATUS_COLOUR: dict[str, str] = {
    "completed": "#34d399",
    "failed": "#f87171",
    "cancelled": "#8892a4",
}

_STATUS_LABEL: dict[str, str] = {
    "completed": "✓ Completed",
    "failed": "✗ Failed",
    "cancelled": "○ Cancelled",
}


def _smtp_config() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_addr": os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", ""),
        "tls": os.environ.get("SMTP_TLS", "starttls"),
        "timeout": int(os.environ.get("SMTP_TIMEOUT", "10")),
    }


class MailDeliveryManager:
    def __init__(self, state_store: "SQLiteStateStore") -> None:
        self._state_store = state_store

    def is_configured(self) -> bool:
        return bool(os.environ.get("SMTP_HOST", ""))

    def fire(self, *, event_type: str, job_row: dict, user_id: str | None) -> None:
        if not self.is_configured():
            return
        if user_id is None:
            return
        toggle = _EVENT_TOGGLE.get(event_type)
        if toggle is None:
            return
        prefs = self._state_store.get_notification_prefs(user_id)
        if not prefs.get(toggle):
            return
        user = self._state_store.get_user_by_id(user_id)
        if not user or not user.get("email"):
            return
        t = threading.Thread(
            target=self._deliver,
            args=(str(user["email"]), event_type, job_row),
            daemon=True,
        )
        t.start()

    def send_test(self, *, to_address: str) -> None:
        cfg = _smtp_config()
        msg = _build_message(
            from_addr=cfg["from_addr"] or to_address,
            to_addr=to_address,
            subject="Groupware Migrator — SMTP test",
            body_text="SMTP is configured correctly. This is a test message.",
            body_html=_render_test_html(),
        )
        _send_smtp(msg, cfg)

    def _deliver(self, to_address: str, event_type: str, job_row: dict) -> None:
        try:
            cfg = _smtp_config()
            status = job_row.get("status", "")
            job_name = job_row.get("job_name") or job_row.get("job_id", "unknown")
            subject = f"Groupware Migrator — Job {_STATUS_LABEL.get(status, status)}: {job_name}"
            msg = _build_message(
                from_addr=cfg["from_addr"] or to_address,
                to_addr=to_address,
                subject=subject,
                body_text=_render_text(job_row),
                body_html=_render_html(job_row),
            )
            _send_smtp(msg, cfg)
        except Exception as exc:
            logger.error("Failed to send notification email to %s: %s", to_address, exc)


def _build_message(
    *, from_addr: str, to_addr: str, subject: str, body_text: str, body_html: str
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))
    return msg


def _send_smtp(msg: MIMEMultipart, cfg: dict) -> None:
    if cfg["tls"] == "ssl":
        conn: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=cfg["timeout"])
    else:
        conn = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
    with conn:
        if cfg["tls"] == "starttls":
            conn.starttls()
        if cfg["user"]:
            conn.login(cfg["user"], cfg["password"])
        conn.send_message(msg)


def _render_text(job_row: dict) -> str:
    status = job_row.get("status", "")
    job_name = job_row.get("job_name") or job_row.get("job_id", "unknown")
    site_url = os.environ.get("SITE_URL", "")
    lines = [
        f"Job: {job_name}",
        f"Status: {_STATUS_LABEL.get(status, status)}",
        f"Items migrated: {job_row.get('migrated_count', 0)}",
        f"Items skipped: {job_row.get('skipped_count', 0)}",
        f"Items failed: {job_row.get('failed_count', 0)}",
    ]
    if job_row.get("last_error"):
        lines.append(f"Error: {job_row['last_error']}")
    if site_url:
        lines.append(f"\nView job: {site_url}/")
    lines.append("\nChange notification preferences in your account settings.")
    return "\n".join(lines)


def _render_test_html() -> str:
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#0f1117;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 20px">'
        '<table width="560" cellpadding="0" cellspacing="0" style="background:#1a1d27;border:1px solid rgba(255,255,255,0.08);border-radius:12px;overflow:hidden;max-width:560px">'
        '<tr><td style="padding:24px 28px;border-bottom:1px solid rgba(255,255,255,0.08)">'
        '<span style="font-size:1.1rem;font-weight:700;color:#e2e8f0">Groupware Migrator</span></td></tr>'
        '<tr><td style="padding:28px">'
        '<p style="color:#e2e8f0;font-size:1rem;font-weight:600;margin:0 0 12px">SMTP test successful</p>'
        '<p style="color:#8892a4;font-size:0.9rem;margin:0">Your SMTP configuration is working correctly.</p>'
        '</td></tr>'
        '<tr><td style="padding:16px 28px;border-top:1px solid rgba(255,255,255,0.08)">'
        '<p style="color:#8892a4;font-size:0.78rem;margin:0">Change notification preferences in your account settings.</p>'
        '</td></tr></table></td></tr></table></body></html>'
    )


def _render_html(job_row: dict) -> str:
    status = job_row.get("status", "")
    job_name = job_row.get("job_name") or job_row.get("job_id", "unknown")
    migrated = job_row.get("migrated_count", 0)
    skipped = job_row.get("skipped_count", 0)
    failed_count = job_row.get("failed_count", 0)
    colour = _STATUS_COLOUR.get(status, "#8892a4")
    label = _STATUS_LABEL.get(status, status)
    site_url = os.environ.get("SITE_URL", "")
    view_btn = (
        f'<tr><td style="padding:8px 28px 24px">'
        f'<a href="{site_url}/" style="display:inline-block;background:#6c8fff;color:#fff;'
        f'text-decoration:none;padding:10px 20px;border-radius:8px;font-size:0.88rem;font-weight:600">'
        f'View job →</a></td></tr>'
    ) if site_url else ""
    error_row = (
        f'<tr><td style="padding:0 28px 16px">'
        f'<p style="color:#f87171;font-size:0.83rem;background:rgba(248,113,113,0.1);'
        f'border:1px solid rgba(248,113,113,0.2);border-radius:6px;padding:10px 14px;'
        f'margin:0;word-break:break-word">{job_row["last_error"]}</p></td></tr>'
    ) if job_row.get("last_error") else ""
    return (
        f'<!DOCTYPE html><html><body style="margin:0;padding:0;background:#0f1117;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 20px">'
        f'<table width="560" cellpadding="0" cellspacing="0" style="background:#1a1d27;border:1px solid rgba(255,255,255,0.08);border-radius:12px;overflow:hidden;max-width:560px">'
        f'<tr><td style="padding:24px 28px;border-bottom:1px solid rgba(255,255,255,0.08)">'
        f'<span style="font-size:1.1rem;font-weight:700;color:#e2e8f0">Groupware Migrator</span></td></tr>'
        f'<tr><td style="padding:28px 28px 16px">'
        f'<p style="color:#e2e8f0;font-size:1.1rem;font-weight:600;margin:0 0 8px">{job_name}</p>'
        f'<span style="display:inline-block;background:{colour}22;border:1px solid {colour}44;'
        f'color:{colour};border-radius:6px;padding:3px 10px;font-size:0.82rem;font-weight:600">{label}</span>'
        f'</td></tr>'
        f'<tr><td style="padding:0 28px 20px">'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:8px">'
        f'<tr>'
        f'<td align="center" style="padding:14px;border-right:1px solid rgba(255,255,255,0.08)">'
        f'<div style="font-size:1.4rem;font-weight:700;color:#34d399">{migrated}</div>'
        f'<div style="font-size:0.72rem;color:#8892a4;margin-top:2px">migrated</div></td>'
        f'<td align="center" style="padding:14px;border-right:1px solid rgba(255,255,255,0.08)">'
        f'<div style="font-size:1.4rem;font-weight:700;color:#fbbf24">{skipped}</div>'
        f'<div style="font-size:0.72rem;color:#8892a4;margin-top:2px">skipped</div></td>'
        f'<td align="center" style="padding:14px">'
        f'<div style="font-size:1.4rem;font-weight:700;color:#f87171">{failed_count}</div>'
        f'<div style="font-size:0.72rem;color:#8892a4;margin-top:2px">failed</div></td>'
        f'</tr></table></td></tr>'
        f'{error_row}'
        f'{view_btn}'
        f'<tr><td style="padding:16px 28px;border-top:1px solid rgba(255,255,255,0.08)">'
        f'<p style="color:#8892a4;font-size:0.78rem;margin:0">Change notification preferences in your account settings.</p>'
        f'</td></tr></table></td></tr></table></body></html>'
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.py -v
```

Expected: All tests pass. Some SMTP tests use mocks so no real server needed.

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/engine/mailer.py tests/test_email_notifications.py
git commit -m "feat: add MailDeliveryManager with HTML email templates"
```

---

### Task 3: Auth router — notification preferences endpoints

**Files:**
- Modify: `src/groupware_migrator/api/routers/auth_router.py`
- Modify: `tests/test_email_notifications.py`

- [ ] **Step 1: Write failing API tests**

Append to `tests/test_email_notifications.py`:

```python
from fastapi.testclient import TestClient
from groupware_migrator.api.app import create_app


def _authed_client(app, email="user@example.com", password="password123") -> tuple:
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


class TestNotificationPrefsAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        store: SQLiteStateStore = self.app.state.state_store
        store.create_user(
            email="user@example.com",
            password_hash=hash_password("password123"),
        )
        self.client = _authed_client(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_get_defaults(self):
        resp = self.client.get("/auth/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["on_completed"])
        self.assertFalse(data["on_failed"])
        self.assertFalse(data["on_cancelled"])

    def test_patch_updates_prefs(self):
        resp = self.client.patch(
            "/auth/notifications",
            json={"on_completed": True, "on_failed": True, "on_cancelled": False},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["on_completed"])
        self.assertTrue(data["on_failed"])
        self.assertFalse(data["on_cancelled"])

    def test_patch_then_get_persists(self):
        self.client.patch(
            "/auth/notifications",
            json={"on_completed": False, "on_failed": True, "on_cancelled": True},
        )
        resp = self.client.get("/auth/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["on_completed"])
        self.assertTrue(data["on_failed"])
        self.assertTrue(data["on_cancelled"])

    def test_requires_auth(self):
        bare = TestClient(self.app, raise_server_exceptions=True)
        resp = bare.get("/auth/notifications")
        self.assertEqual(resp.status_code, 401)
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.TestNotificationPrefsAPI -v
```

Expected: `404 Not Found` on `/auth/notifications`

- [ ] **Step 3: Add endpoints to `auth_router.py`**

In `src/groupware_migrator/api/routers/auth_router.py`, add the Pydantic model after `TotpDisablePayload`:

```python
class NotificationPrefsPayload(BaseModel):
    on_completed: bool
    on_failed: bool
    on_cancelled: bool
```

Then, inside the `create_auth_router` function, add these two routes after the existing TOTP routes:

```python
    @router.get("/auth/notifications")
    def get_notification_prefs(current_user: dict = Depends(require_user)) -> dict:
        return state_store.get_notification_prefs(str(current_user["sub"]))

    @router.patch("/auth/notifications")
    def set_notification_prefs(
        payload: NotificationPrefsPayload,
        current_user: dict = Depends(require_user),
    ) -> dict:
        state_store.set_notification_prefs(
            str(current_user["sub"]),
            on_completed=payload.on_completed,
            on_failed=payload.on_failed,
            on_cancelled=payload.on_cancelled,
        )
        return state_store.get_notification_prefs(str(current_user["sub"]))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.TestNotificationPrefsAPI -v
```

Expected: `4 tests … OK`

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/api/routers/auth_router.py tests/test_email_notifications.py
git commit -m "feat: add GET/PATCH /auth/notifications endpoints"
```

---

### Task 4: Admin router — SMTP test endpoint

**Files:**
- Modify: `src/groupware_migrator/api/routers/admin_router.py`
- Modify: `tests/test_email_notifications.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_email_notifications.py`:

```python
class TestSmtpTestEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        store: SQLiteStateStore = self.app.state.state_store
        store.create_user(
            email="admin@example.com",
            password_hash=hash_password("adminpass"),
            is_admin=True,
        )
        self.client = _authed_client(self.app, email="admin@example.com", password="adminpass")

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_503_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SMTP_HOST", None)
            resp = self.client.post("/api/admin/smtp/test")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("SMTP_HOST", resp.json()["detail"])

    def test_returns_200_on_success(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("groupware_migrator.engine.mailer._send_smtp"):
                resp = self.client.post("/api/admin/smtp/test")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["sent_to"], "admin@example.com")

    def test_returns_502_on_smtp_error(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch(
                "groupware_migrator.engine.mailer._send_smtp",
                side_effect=smtplib.SMTPException("connection failed"),
            ):
                resp = self.client.post("/api/admin/smtp/test")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("SMTP error", resp.json()["detail"])
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.TestSmtpTestEndpoint -v
```

Expected: `404 Not Found` on `/api/admin/smtp/test`

- [ ] **Step 3: Update `create_admin_router` signature and add endpoint**

In `src/groupware_migrator/api/routers/admin_router.py`, add the import at the top:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from groupware_migrator.api.auth import require_admin, require_user
from groupware_migrator.engine.state import SQLiteStateStore, hash_password

if TYPE_CHECKING:
    from groupware_migrator.engine.mailer import MailDeliveryManager
```

Change the function signature:

```python
def create_admin_router(
    state_store: SQLiteStateStore,
    mail_manager: "MailDeliveryManager | None" = None,
) -> APIRouter:
```

Then add this route inside `create_admin_router`, after the existing cleanup route:

```python
    @router.post("/admin/smtp/test")
    def smtp_test(admin: dict = Depends(require_admin)) -> dict:
        if mail_manager is None or not mail_manager.is_configured():
            raise HTTPException(
                status_code=503,
                detail="SMTP is not configured (SMTP_HOST not set).",
            )
        user = state_store.get_user_by_id(str(admin["sub"]))
        to_address = str(user["email"]) if user and user.get("email") else ""
        if not to_address:
            raise HTTPException(status_code=400, detail="Admin account has no email address.")
        try:
            mail_manager.send_test(to_address=to_address)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"SMTP error: {exc}") from exc
        return {"ok": True, "sent_to": to_address}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.TestSmtpTestEndpoint -v
```

Expected: `3 tests … OK`

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/api/routers/admin_router.py tests/test_email_notifications.py
git commit -m "feat: add POST /admin/smtp/test endpoint"
```

---

### Task 5: Wire `MailDeliveryManager` into `background.py` and `app.py`

**Files:**
- Modify: `src/groupware_migrator/engine/background.py`
- Modify: `src/groupware_migrator/api/app.py`
- Modify: `tests/test_email_notifications.py`

- [ ] **Step 1: Write failing integration test**

Append to `tests/test_email_notifications.py`:

```python
from groupware_migrator.engine.mailer import MailDeliveryManager
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.runner import MigrationRunner


class TestBackgroundJobManagerMailWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = _store(self._tmp.name)
        self.user_id = _user(self.store)
        self.store.set_notification_prefs(
            self.user_id, on_completed=True, on_failed=True, on_cancelled=False
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_mail_fired_on_job_done(self):
        mail_mgr = MailDeliveryManager(self.store)
        fired_events = []

        def fake_fire(*, event_type, job_row, user_id):
            fired_events.append(event_type)

        mail_mgr.fire = fake_fire  # type: ignore[method-assign]

        runner = MigrationRunner(state_store=self.store)
        mgr = BackgroundJobManager(
            state_store=self.store,
            runner=runner,
            mail_manager=mail_mgr,
        )
        # Simulate _on_done by injecting a fake completed future
        import concurrent.futures
        job_id = self.store.create_job(
            request=_make_request(),
            plan=__import__("groupware_migrator.models", fromlist=["MigrationPlan"]).MigrationPlan(),
            user_id=self.user_id,
        )
        self.store.update_job_status(
            job_id,
            status=__import__("groupware_migrator.models", fromlist=["JobStatus"]).JobStatus.COMPLETED,
        )
        with self._lock_bypass(mgr, job_id):
            f = concurrent.futures.Future()
            f.set_result(None)
            mgr._job_contexts[job_id] = (_make_request(), 0)
            mgr._on_done(job_id, f)

        self.assertIn("job.completed", fired_events)

    @staticmethod
    def _lock_bypass(mgr, job_id):
        import contextlib
        @contextlib.contextmanager
        def ctx():
            yield
        return ctx()
```

The test above is complex because `_on_done` needs `_job_contexts`. A simpler integration approach: verify that `BackgroundJobManager.__init__` accepts `mail_manager` without error, and that `_on_done` calls `mail_manager.fire` when a job completes.

Replace the above test with this simpler version:

```python
from groupware_migrator.engine.mailer import MailDeliveryManager
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.models import JobStatus, MigrationPlan


class TestBackgroundJobManagerMailWiring(unittest.TestCase):
    def test_accepts_mail_manager_param(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            runner = MigrationRunner(state_store=store)
            mail_mgr = MailDeliveryManager(store)
            mgr = BackgroundJobManager(
                state_store=store,
                runner=runner,
                mail_manager=mail_mgr,
            )
            self.assertIs(mgr._mail_manager, mail_mgr)

    def test_mail_manager_defaults_to_none(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            runner = MigrationRunner(state_store=store)
            mgr = BackgroundJobManager(state_store=store, runner=runner)
            self.assertIsNone(mgr._mail_manager)

    def test_on_done_calls_fire(self):
        import concurrent.futures
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            user_id = _user(store)
            runner = MigrationRunner(state_store=store)
            mail_mgr = MailDeliveryManager(store)

            fired = []
            mail_mgr.fire = lambda **kw: fired.append(kw["event_type"])  # type: ignore[method-assign]

            mgr = BackgroundJobManager(state_store=store, runner=runner, mail_manager=mail_mgr)

            request = _make_request()
            job_id = store.create_job(request=request, plan=MigrationPlan(), user_id=user_id)
            store.update_job_status(job_id, status=JobStatus.COMPLETED)

            # Inject context as _submit would and call _on_done directly
            mgr._job_contexts[job_id] = (request, 0)
            f = concurrent.futures.Future()
            f.set_result(None)
            mgr._on_done(job_id, f)

            self.assertIn("job.completed", fired)
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.TestBackgroundJobManagerMailWiring -v
```

Expected: `TypeError: BackgroundJobManager.__init__() got an unexpected keyword argument 'mail_manager'`

- [ ] **Step 3: Update `background.py`**

In `src/groupware_migrator/engine/background.py`, add to the `TYPE_CHECKING` block:

```python
if TYPE_CHECKING:
    from groupware_migrator.engine.mailer import MailDeliveryManager
    from groupware_migrator.engine.webhooks import WebhookDeliveryManager
```

Update `__init__` to accept `mail_manager`:

```python
    def __init__(
        self,
        *,
        state_store: SQLiteStateStore,
        runner: MigrationRunner,
        max_workers: int = 4,
        webhook_manager: "WebhookDeliveryManager | None" = None,
        mail_manager: "MailDeliveryManager | None" = None,
    ):
        self._state_store = state_store
        self._runner = runner
        self._webhook_manager = webhook_manager
        self._mail_manager = mail_manager
        self._executor = ThreadPoolExecutor(
            max_workers=max(max_workers, 1),
            thread_name_prefix="migration-worker",
        )
        self._futures: dict[str, Future] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._job_contexts: dict[str, tuple[MigrationRequest, int]] = {}
        self._lock = threading.Lock()
        self._accepting = True
```

In `_on_done`, update the event_type derivation to handle cancellation, and add mail firing after the webhook block. Replace the section starting with `job_row = self._state_store.get_job(job_id)` through the end of the webhook try/except:

```python
        job_row = self._state_store.get_job(job_id)
        if job_row:
            status = job_row.get("status", "")
            if status == JobStatus.COMPLETED.value:
                event_type = "job.completed"
            elif status == JobStatus.CANCELLED.value:
                event_type = "job.cancelled"
            else:
                event_type = "job.failed"
            user_id = job_row.get("user_id") or None
            payload = {
                "job_id": job_id,
                "job_name": job_row.get("job_name"),
                "status": status,
                "migrated_count": job_row.get("migrated_count", 0),
                "skipped_count": job_row.get("skipped_count", 0),
                "failed_count": job_row.get("failed_count", 0),
                "last_error": job_row.get("last_error"),
                "finished_at": job_row.get("finished_at"),
            }
            if self._webhook_manager is not None:
                try:
                    self._webhook_manager.fire(event_type=event_type, payload=payload, user_id=user_id)
                except Exception as exc:
                    logger.error("Failed to fire webhooks for job %s: %s", job_id, exc)
            if self._mail_manager is not None:
                try:
                    self._mail_manager.fire(event_type=event_type, job_row=job_row, user_id=user_id)
                except Exception as exc:
                    logger.error("Failed to fire email for job %s: %s", job_id, exc)
```

- [ ] **Step 4: Update `app.py`**

In `src/groupware_migrator/api/app.py`, add the import:

```python
from groupware_migrator.engine.mailer import MailDeliveryManager
```

Then in `create_app`, update the instantiation block (currently around line 73):

```python
    webhook_manager = WebhookDeliveryManager(state_store)
    mail_manager = MailDeliveryManager(state_store)
    background_jobs = BackgroundJobManager(
        state_store=state_store,
        runner=runner,
        webhook_manager=webhook_manager,
        mail_manager=mail_manager,
    )
```

And update the `create_admin_router` call to pass `mail_manager`:

```python
    admin_router = create_admin_router(state_store, mail_manager=mail_manager)
```

Also add to `app.state`:
```python
    app.state.mail_manager = mail_manager
```

- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=src python3 -m unittest tests/test_email_notifications.TestBackgroundJobManagerMailWiring -v
```

Expected: `3 tests … OK`

- [ ] **Step 6: Run full suite to check for regressions**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: All existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/groupware_migrator/engine/background.py src/groupware_migrator/api/app.py tests/test_email_notifications.py
git commit -m "feat: wire MailDeliveryManager into BackgroundJobManager and app factory"
```

---

### Task 6: UI — Notification Preferences section

**Files:**
- Modify: `src/groupware_migrator/api/static/scheduler.html`
- Modify: `src/groupware_migrator/api/static/js/scheduler.js`

- [ ] **Step 1: Add HTML section to `scheduler.html`**

In `scheduler.html`, find the closing `</div>` of the `<!-- 2FA / Security section -->` block (the one just before `</aside>`). Insert the following new section **after** that closing `</div>` and before `</aside>`:

```html
          <!-- Email Notification Preferences -->
          <div class="dashboard-section">
            <h2>Email Notifications</h2>
            <div id="notif-unavailable" style="display:none">
              <p class="helper-text" style="color:#fbbf24">Email notifications are not configured on this server. Ask your administrator to set <code>SMTP_HOST</code>.</p>
            </div>
            <div id="notif-prefs" style="display:none">
              <p class="helper-text">Choose which job events send you an email.</p>
              <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px">
                <label class="check"><input type="checkbox" id="notif-completed" /> Email me when a job <strong>completes successfully</strong></label>
                <label class="check"><input type="checkbox" id="notif-failed" /> Email me when a job <strong>fails</strong></label>
                <label class="check"><input type="checkbox" id="notif-cancelled" /> Email me when a job is <strong>cancelled</strong></label>
              </div>
              <p id="notif-feedback" class="feedback"></p>
            </div>
            <div id="notif-smtp-test" style="display:none;margin-top:10px">
              <button id="notif-test-btn" class="btn secondary" style="font-size:0.83rem">Send test email to my address</button>
              <p id="notif-test-feedback" class="feedback"></p>
            </div>
            <p id="notif-loading" style="color:#8892a4;font-size:0.85rem">Loading…</p>
          </div>
```

- [ ] **Step 2: Add JS to `scheduler.js`**

In `src/groupware_migrator/api/static/js/scheduler.js`, add the following functions **before** the final `document.addEventListener("DOMContentLoaded", bootstrap)` line (or before the `bootstrap()` call at the bottom):

```js
// ─── Notification Preferences ───────────────────────────────────────────────

async function loadNotificationPrefs(isAdmin) {
  const loading = document.getElementById("notif-loading");
  const unavailable = document.getElementById("notif-unavailable");
  const prefs = document.getElementById("notif-prefs");
  const smtpTest = document.getElementById("notif-smtp-test");
  if (!loading) return;
  try {
    const data = await requestJSON("/auth/notifications");
    loading.style.display = "none";
    prefs.style.display = "";
    document.getElementById("notif-completed").checked = !!data.on_completed;
    document.getElementById("notif-failed").checked = !!data.on_failed;
    document.getElementById("notif-cancelled").checked = !!data.on_cancelled;
    if (isAdmin) smtpTest.style.display = "";
  } catch (e) {
    loading.style.display = "none";
    unavailable.style.display = "";
  }
}

async function saveNotificationPref() {
  const feedback = document.getElementById("notif-feedback");
  const payload = {
    on_completed: document.getElementById("notif-completed").checked,
    on_failed: document.getElementById("notif-failed").checked,
    on_cancelled: document.getElementById("notif-cancelled").checked,
  };
  try {
    await requestJSON("/auth/notifications", { method: "PATCH", body: JSON.stringify(payload) });
    if (feedback) { feedback.textContent = "Saved."; feedback.className = "feedback ok"; setTimeout(() => { feedback.textContent = ""; }, 2000); }
  } catch (e) {
    if (feedback) { feedback.textContent = e.message; feedback.className = "feedback err"; }
  }
}

function bindNotifHandlers(isAdmin) {
  ["notif-completed", "notif-failed", "notif-cancelled"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", saveNotificationPref);
  });
  if (isAdmin) {
    const btn = document.getElementById("notif-test-btn");
    if (btn) btn.addEventListener("click", async () => {
      const fb = document.getElementById("notif-test-feedback");
      btn.disabled = true;
      try {
        const data = await requestJSON("/api/admin/smtp/test", { method: "POST" });
        if (fb) { fb.textContent = `Test email sent to ${data.sent_to}`; fb.className = "feedback ok"; }
      } catch (e) {
        if (fb) { fb.textContent = e.message; fb.className = "feedback err"; }
      } finally {
        btn.disabled = false;
      }
    });
  }
}
```

- [ ] **Step 3: Update `bootstrap` to call the new functions**

In `scheduler.js`, find the `bootstrap` function and update it:

```js
async function bootstrap() {
  let isAdmin = false;
  try {
    const me = await requestJSON("/auth/me");
    isAdmin = !!me.is_admin;
    if (isAdmin) {
      const link = document.getElementById("admin-link");
      if (link) link.style.display = "";
    }
  } catch (_) {}
  await Promise.all([loadSchedules(), loadWebhooks(), loadTotpStatus(), loadNotificationPrefs(isAdmin)]);
  bindScheduleForm();
  bindWebhookForm();
  bindTotpHandlers();
  bindNotifHandlers(isAdmin);
}
```

- [ ] **Step 4: Run the full test suite**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/api/static/scheduler.html src/groupware_migrator/api/static/js/scheduler.js
git commit -m "feat: add email notification preferences UI to scheduler page"
```

---

### Task 7: Final integration — run all tests and push

- [ ] **Step 1: Run the complete test suite**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -10
```

Expected: All tests pass (159 existing + new email notification tests).

- [ ] **Step 2: Update README env vars table**

In `README.md`, in the environment variables table, add two new rows after the `VAULT_KEY` row:

```markdown
| `SMTP_HOST` | — | Hostname of the SMTP server. Feature is disabled when absent. |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP auth username |
| `SMTP_PASSWORD` | — | SMTP auth password |
| `SMTP_FROM` | `SMTP_USER` | Sender address shown in email clients |
| `SMTP_TLS` | `starttls` | TLS mode: `starttls` \| `ssl` \| `none` |
| `SMTP_TIMEOUT` | `10` | Seconds before SMTP connection timeout |
| `SITE_URL` | — | Base URL used in email "View job" links (e.g. `https://migrate.example.com`) |
```

Also add to the Auth API table:

```markdown
| `GET` | `/auth/notifications` | Get your email notification preferences |
| `PATCH` | `/auth/notifications` | Update your email notification preferences |
```

And add to the Admin API table section or create a note:

```markdown
| `POST` | `/api/admin/smtp/test` | Send a test email to verify SMTP config (admin only) |
```

- [ ] **Step 3: Update ROADMAP.md and roadmap.html**

In `ROADMAP.md`, move "Email notifications (SMTP)" from the Remaining Gaps table into Phase 5's Notifications section:

```markdown
- `POST /api/admin/smtp/test` to verify SMTP config; per-user opt-in toggles via `GET/PATCH /auth/notifications`
- Env vars: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_TIMEOUT`, `SITE_URL`
```

Remove the "Email notifications (SMTP)" row from the Remaining Gaps table.

In `roadmap.html`, find the email notifications feature in Phase 5 and remove the `(deferred — SMTP required)` note. Remove the item from the "Remaining Gaps" section.

In the foundation "What's Shipped" grid in `roadmap.html`, add:
```html
<div class="foundation-item">SMTP email notifications (per-user opt-in)</div>
```

- [ ] **Step 4: Final commit and push**

```bash
git add -A
git commit -m "feat: email notifications — SMTP, per-user prefs, HTML templates, admin test endpoint"
git push origin main
```
