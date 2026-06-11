from __future__ import annotations

import logging
import os
import smtplib
import threading
from html import escape
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
        f'margin:0;word-break:break-word">{escape(str(job_row["last_error"]))}</p></td></tr>'
    ) if job_row.get("last_error") else ""
    return (
        f'<!DOCTYPE html><html><body style="margin:0;padding:0;background:#0f1117;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 20px">'
        f'<table width="560" cellpadding="0" cellspacing="0" style="background:#1a1d27;border:1px solid rgba(255,255,255,0.08);border-radius:12px;overflow:hidden;max-width:560px">'
        f'<tr><td style="padding:24px 28px;border-bottom:1px solid rgba(255,255,255,0.08)">'
        f'<span style="font-size:1.1rem;font-weight:700;color:#e2e8f0">Groupware Migrator</span></td></tr>'
        f'<tr><td style="padding:28px 28px 16px">'
        f'<p style="color:#e2e8f0;font-size:1.1rem;font-weight:600;margin:0 0 8px">{escape(str(job_name))}</p>'
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
