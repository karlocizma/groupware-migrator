# Email Notifications Design

**Date:** 2026-06-11  
**Status:** Approved

## Goal

Allow users to receive HTML emails when their migration jobs complete, fail, or are cancelled. Admin configures SMTP system-wide via env vars; each user independently opts into the events they care about.

## Architecture

Three new pieces, one change to an existing one:

1. **`engine/mailer.py`** (new) — `MailDeliveryManager`; sends emails in daemon threads
2. **`engine/state.py`** (modified) — `notification_prefs` table + two query methods
3. **`api/routers/auth_router.py`** (modified) — two new `/auth/notifications` endpoints
4. **`api/routers/admin_router.py`** (modified) — `POST /admin/smtp/test` endpoint
5. **`engine/background.py`** (modified) — wire `mail_manager` alongside existing `webhook_manager`
6. **`api/app.py`** (modified) — instantiate `MailDeliveryManager`, pass to `BackgroundJobManager`
7. **`api/static/scheduler.html`** (modified) — "Notification Preferences" section (3 toggles)

No new Python dependencies — uses stdlib `smtplib` only.

---

## SMTP Configuration (env vars)

| Variable | Default | Required | Notes |
|---|---|---|---|
| `SMTP_HOST` | — | Yes (to enable) | Feature silently disabled when absent |
| `SMTP_PORT` | `587` | No | |
| `SMTP_USER` | — | No | Some servers don't require auth |
| `SMTP_PASSWORD` | — | No | |
| `SMTP_FROM` | `SMTP_USER` | No | Sender display address |
| `SMTP_TLS` | `starttls` | No | `starttls` \| `ssl` \| `none` |
| `SMTP_TIMEOUT` | `10` | No | Seconds before connection timeout |

`SMTP_HOST` being absent means `MailDeliveryManager.fire()` is a no-op. No error is raised; the app runs normally without email.

---

## Database

### New table: `notification_prefs`

```sql
CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    on_completed INTEGER NOT NULL DEFAULT 0,
    on_failed    INTEGER NOT NULL DEFAULT 0,
    on_cancelled INTEGER NOT NULL DEFAULT 0
);
```

One row per user, created on first `PATCH /auth/notifications` call (upsert). Users with no row default to all-off.

### New state methods

- `get_notification_prefs(user_id: str) -> dict` — returns `{on_completed, on_failed, on_cancelled}` (all False if no row)
- `set_notification_prefs(user_id: str, on_completed: bool, on_failed: bool, on_cancelled: bool) -> None` — upsert

---

## `engine/mailer.py`

```python
class MailDeliveryManager:
    def __init__(self, state_store: SQLiteStateStore) -> None: ...

    def is_configured(self) -> bool:
        """Return True if SMTP_HOST env var is set."""

    def fire(self, *, event_type: str, job_row: dict, user_id: str | None) -> None:
        """Non-blocking: check prefs, spawn daemon thread to send if enabled."""

    def send_test(self, *, to_address: str) -> None:
        """Synchronous test send. Raises on any SMTP error."""
```

### Sending logic

1. If `SMTP_HOST` is not set → return immediately (no-op).
2. If `user_id` is None → return (no address to send to).
3. Look up `notification_prefs` for `user_id`; check the relevant toggle.
4. Look up user email from `users` table.
5. Spawn daemon `threading.Thread` targeting `_deliver(to, event_type, job_row)`.

### `_deliver` internals

- Build multipart `MIMEMultipart("alternative")` with `text/plain` and `text/html` parts.
- Connect via `smtplib.SMTP` (STARTTLS) or `smtplib.SMTP_SSL` depending on `SMTP_TLS`.
- Login if `SMTP_USER` is set.
- Send; catch and log all exceptions — email failure must never propagate to job recording.

### Event → toggle mapping

| `event_type` | toggle checked |
|---|---|
| `job.completed` | `on_completed` |
| `job.failed` | `on_failed` |
| `job.cancelled` | `on_cancelled` |

---

## HTML Email Template

Inline-styled for broad email client compatibility. Content:

- Header: "Groupware Migrator" wordmark
- Status badge: green (completed) / red (failed) / grey (cancelled)
- Job name (or ID if no name)
- Stats row: Items migrated · Items skipped · Items failed
- Duration
- "View job" button linking to `{SITE_URL}/` (falls back to `#` if `SITE_URL` env var not set)
- Footer: unsubscribe hint ("Change notification preferences in your account settings")

Plain-text fallback contains the same information without styling.

---

## API Endpoints

### `GET /auth/notifications`

Returns the current user's notification preferences.

**Response 200:**
```json
{
  "on_completed": false,
  "on_failed": true,
  "on_cancelled": false
}
```

### `PATCH /auth/notifications`

Updates the current user's preferences. All three fields required (full replace, not partial).

**Request body:**
```json
{
  "on_completed": true,
  "on_failed": true,
  "on_cancelled": false
}
```

**Response 200:** Same shape as GET.

### `POST /admin/smtp/test`

Sends a test email to the calling admin's own account address. Synchronous — returns success or error immediately so the admin can confirm SMTP is working.

**Response 200:**
```json
{"ok": true, "sent_to": "admin@example.com"}
```

**Response 502** (SMTP error):
```json
{"detail": "SMTP error: [Errno 111] Connection refused"}
```

**Response 503** (SMTP not configured):
```json
{"detail": "SMTP is not configured (SMTP_HOST not set)."}
```

---

## `background.py` changes

`BackgroundJobManager.__init__` gains `mail_manager: MailDeliveryManager | None = None`.

In `_on_done()`, after firing webhooks:

```python
if self._mail_manager is not None:
    try:
        self._mail_manager.fire(
            event_type=event_type,
            job_row=job_row,
            user_id=user_id,
        )
    except Exception as exc:
        logger.error("Failed to fire email for job %s: %s", job_id, exc)
```

---

## `app.py` changes

```python
mail_manager = MailDeliveryManager(state_store)
background_jobs = BackgroundJobManager(
    ...,
    webhook_manager=webhook_manager,
    mail_manager=mail_manager,
)
```

---

## UI: Notification Preferences section

Added to `scheduler.html` below the TOTP 2FA section. Three checkboxes:

- ☐ Email me when a job **completes successfully**
- ☐ Email me when a job **fails**
- ☐ Email me when a job is **cancelled**

Auto-saves on change (PATCH on checkbox click). Disabled with a note ("Email notifications are not configured on this server") when `GET /auth/notifications` returns a 503 or the server lacks SMTP config.

The admin SMTP test button appears only for admins: "Send test email to my address".

---

## Error Handling

- SMTP not configured → silent no-op on `fire()`; 503 on `POST /admin/smtp/test`
- SMTP connection failure → logged at ERROR level; never raises
- User has no email (impossible given current schema, but guarded) → skip silently
- Preference lookup failure → default all-off (safe fallback)

---

## Testing

- Unit tests for `MailDeliveryManager` using `unittest.mock.patch("smtplib.SMTP")`
- Tests for both STARTTLS and SSL paths
- Test that `fire()` is a no-op when `SMTP_HOST` is absent
- Test that `fire()` is a no-op when the user's toggle is off
- Test `send_test()` raises on SMTP failure
- Tests for `get_notification_prefs` / `set_notification_prefs` state methods
- Tests for `GET /auth/notifications` and `PATCH /auth/notifications` endpoints
- Test that `POST /admin/smtp/test` returns 503 when unconfigured
