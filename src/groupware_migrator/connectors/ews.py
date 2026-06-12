"""Exchange Web Services (EWS) source connector for on-premises Exchange Server.

Supports migrating mail, calendar, contacts, and tasks from Exchange Server
2010–2019 (on-premises) using the EWS SOAP API.

Requires the ``exchangelib`` package:
    pip install "groupware-migrator[ews]"

Connection config:
  host        — Exchange server hostname (e.g. mail.corp.example.com).
                Leave blank to use EWS autodiscover.
  username    — UPN / email (user@corp.example.com) or DOMAIN\\\\username
  password    — User password
  auth_mode   — "password" (NTLM/Negotiate, default for on-prem)
"""
from __future__ import annotations

import uuid
from typing import Iterator

from groupware_migrator.connectors.base import SourceConnector
from groupware_migrator.models import CollectionSnapshot, SourceItem, SourceProtocol
from groupware_migrator.models.domain import ConnectionConfig, WorkloadType

_PRODID = "-//groupware-migrator//EWS//EN"


def _require_exchangelib():
    try:
        import exchangelib
        return exchangelib
    except ImportError:
        raise ImportError(
            "exchangelib is required for EWS migrations. "
            'Install it with: pip install "groupware-migrator[ews]"'
        ) from None


def _ews_dt_to_ical(dt) -> str:
    if dt is None:
        return ""
    try:
        utc = dt.astimezone(dt.tzinfo.__class__.UTC if hasattr(dt, "astimezone") else None)
    except Exception:
        utc = dt
    return utc.strftime("%Y%m%dT%H%M%SZ")


def _ews_date_to_ical(d) -> str:
    if d is None:
        return ""
    return d.strftime("%Y%m%d")


def _escape_ical(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _calendar_item_to_ical(item) -> bytes:
    uid = getattr(item, "uid", None) or str(uuid.uuid4())
    subject = _escape_ical(str(getattr(item, "subject", "") or ""))
    start = getattr(item, "start", None)
    end = getattr(item, "end", None)
    is_all_day = bool(getattr(item, "is_all_day", False))
    location = _escape_ical(str(getattr(item, "location", "") or ""))
    body_obj = getattr(item, "body", None)
    body_text = _escape_ical(str(body_obj).strip()) if body_obj is not None else ""
    organizer = getattr(item, "organizer", None)
    organizer_email = str(getattr(organizer, "email_address", "") or "") if organizer else ""

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{subject}",
    ]
    if is_all_day:
        if start:
            lines.append(f"DTSTART;VALUE=DATE:{_ews_date_to_ical(start.date())}")
        if end:
            lines.append(f"DTEND;VALUE=DATE:{_ews_date_to_ical(end.date())}")
    else:
        if start:
            lines.append(f"DTSTART:{_ews_dt_to_ical(start)}")
        if end:
            lines.append(f"DTEND:{_ews_dt_to_ical(end)}")
    if location:
        lines.append(f"LOCATION:{location}")
    if body_text:
        lines.append(f"DESCRIPTION:{body_text}")
    if organizer_email:
        lines.append(f"ORGANIZER:mailto:{organizer_email}")
    for attr, role in (("required_attendees", "REQ-PARTICIPANT"), ("optional_attendees", "OPT-PARTICIPANT")):
        for attendee in (getattr(item, attr, None) or []):
            mb = getattr(attendee, "mailbox", None)
            email = str(getattr(mb, "email_address", "") or "") if mb else ""
            if email:
                lines.append(f"ATTENDEE;ROLE={role}:mailto:{email}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines).encode("utf-8")


def _task_to_ical(item) -> bytes:
    uid = getattr(item, "uid", None) or str(uuid.uuid4())
    subject = _escape_ical(str(getattr(item, "subject", "") or ""))
    due = getattr(item, "due_date", None)
    start = getattr(item, "start_date", None)
    percent = int(getattr(item, "percent_complete", 0) or 0)
    status_map = {
        "NotStarted": "NEEDS-ACTION",
        "InProgress": "IN-PROCESS",
        "Completed": "COMPLETED",
        "WaitingOnOthers": "IN-PROCESS",
        "Deferred": "NEEDS-ACTION",
    }
    ical_status = status_map.get(str(getattr(item, "status", "NotStarted") or "NotStarted"), "NEEDS-ACTION")
    body_obj = getattr(item, "body", None)
    body_text = _escape_ical(str(body_obj).strip()) if body_obj is not None else ""

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "BEGIN:VTODO",
        f"UID:{uid}",
        f"SUMMARY:{subject}",
        f"STATUS:{ical_status}",
        f"PERCENT-COMPLETE:{percent}",
    ]
    if start:
        lines.append(f"DTSTART;VALUE=DATE:{_ews_date_to_ical(start)}")
    if due:
        lines.append(f"DUE;VALUE=DATE:{_ews_date_to_ical(due)}")
    if body_text:
        lines.append(f"DESCRIPTION:{body_text}")
    lines += ["END:VTODO", "END:VCALENDAR"]
    return "\r\n".join(lines).encode("utf-8")


def _contact_to_vcard(item) -> bytes:
    given = str(getattr(item, "given_name", "") or "")
    surname = str(getattr(item, "surname", "") or "")
    display = str(getattr(item, "display_name", "") or f"{given} {surname}".strip())
    company = str(getattr(item, "company_name", "") or "")
    title = str(getattr(item, "job_title", "") or "")

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{display}",
        f"N:{surname};{given};;;",
    ]
    if company:
        lines.append(f"ORG:{company}")
    if title:
        lines.append(f"TITLE:{title}")

    _email_type = {"EmailAddress1": "WORK", "EmailAddress2": "HOME", "EmailAddress3": "OTHER"}
    for ea in (getattr(item, "email_addresses", None) or []):
        email = str(getattr(ea, "email", None) or getattr(ea, "email_address", "") or "")
        label = str(getattr(ea, "label", "EmailAddress1") or "EmailAddress1")
        if email:
            lines.append(f"EMAIL;TYPE={_email_type.get(label, 'INTERNET')}:{email}")

    _tel_type = {
        "MobilePhone": "CELL", "BusinessPhone": "WORK", "HomePhone": "HOME",
        "BusinessPhone2": "WORK", "HomePhone2": "HOME", "BusinessFax": "FAX",
    }
    for pn in (getattr(item, "phone_numbers", None) or []):
        number = str(getattr(pn, "phone_number", "") or "")
        label = str(getattr(pn, "label", "") or "")
        if number:
            lines.append(f"TEL;TYPE={_tel_type.get(label, 'VOICE')}:{number}")

    _addr_type = {"Business": "WORK", "Home": "HOME", "Other": "OTHER"}
    for pa in (getattr(item, "physical_addresses", None) or []):
        label = str(getattr(pa, "label", "") or "")
        street = str(getattr(pa, "street", "") or "")
        city = str(getattr(pa, "city", "") or "")
        state = str(getattr(pa, "state", "") or "")
        zipcode = str(getattr(pa, "zipcode", "") or "")
        country = str(getattr(pa, "country_or_region", "") or "")
        if any([street, city, state, zipcode, country]):
            lines.append(f"ADR;TYPE={_addr_type.get(label, 'WORK')}:;;{street};{city};{state};{zipcode};{country}")

    lines.append("END:VCARD")
    return "\r\n".join(lines).encode("utf-8")


class EwsSourceConnector(SourceConnector):
    """Source connector for on-premises Exchange Server via EWS."""

    protocol = SourceProtocol.EWS

    def __init__(self, config: ConnectionConfig, *, workload: WorkloadType = WorkloadType.MAIL) -> None:
        self._config = config
        self._workload = workload
        self._account = None

    def _get_account(self):
        if self._account is not None:
            return self._account
        ews = _require_exchangelib()
        credentials = ews.Credentials(
            username=self._config.username,
            password=self._config.password or "",
        )
        if self._config.host:
            config = ews.Configuration(
                server=self._config.host,
                credentials=credentials,
            )
            self._account = ews.Account(
                primary_smtp_address=self._config.username,
                config=config,
                autodiscover=False,
                access_type=ews.DELEGATE,
            )
        else:
            self._account = ews.Account(
                primary_smtp_address=self._config.username,
                credentials=credentials,
                autodiscover=True,
                access_type=ews.DELEGATE,
            )
        return self._account

    def validate(self) -> None:
        account = self._get_account()
        _ = account.inbox.total_count

    def list_mailboxes(self) -> list[CollectionSnapshot]:
        return self.list_collections()

    def list_collections(self) -> list[CollectionSnapshot]:
        account = self._get_account()
        if self._workload == WorkloadType.MAIL:
            return self._list_mail_folders(account)
        if self._workload == WorkloadType.CALENDAR:
            return [CollectionSnapshot(name="Calendar", estimated_items=account.calendar.total_count)]
        if self._workload == WorkloadType.CONTACTS:
            return [CollectionSnapshot(name="Contacts", estimated_items=account.contacts.total_count)]
        if self._workload == WorkloadType.TASKS:
            return [CollectionSnapshot(name="Tasks", estimated_items=account.tasks.total_count)]
        return []

    def _list_mail_folders(self, account) -> list[CollectionSnapshot]:
        snapshots = []
        try:
            for folder in account.root.walk():
                name = getattr(folder, "name", "") or ""
                if not name:
                    continue
                folder_class = getattr(folder, "folder_class", None)
                if folder_class and not folder_class.startswith("IPF.Note"):
                    continue
                total = getattr(folder, "total_count", 0) or 0
                snapshots.append(CollectionSnapshot(name=name, estimated_items=total))
        except Exception:
            snapshots.append(
                CollectionSnapshot(name="Inbox", estimated_items=account.inbox.total_count)
            )
        return snapshots

    def _find_folder(self, account, name: str):
        try:
            for folder in account.root.walk():
                if getattr(folder, "name", "") == name:
                    return folder
        except Exception:
            pass
        return account.inbox

    def iter_messages(self, mailbox: str, resume_from: str | None = None) -> Iterator[SourceItem]:
        return self.iter_items(mailbox, resume_from)

    def iter_items(self, collection: str, resume_from: str | None = None) -> Iterator[SourceItem]:
        account = self._get_account()
        if self._workload == WorkloadType.MAIL:
            yield from self._iter_mail(account, collection, resume_from)
        elif self._workload == WorkloadType.CALENDAR:
            yield from self._iter_calendar(account, resume_from)
        elif self._workload == WorkloadType.CONTACTS:
            yield from self._iter_contacts(account, resume_from)
        elif self._workload == WorkloadType.TASKS:
            yield from self._iter_tasks(account, resume_from)

    def _iter_mail(self, account, folder_name: str, resume_from: str | None) -> Iterator[SourceItem]:
        folder = self._find_folder(account, folder_name)
        items = folder.all().order_by("datetime_received").only(
            "id", "changekey", "message_id", "datetime_received", "mime_content"
        )
        for msg in items:
            item_id = msg.id
            if resume_from and item_id <= resume_from:
                continue
            try:
                raw = msg.mime_content or b""
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
            except Exception:
                raw = b""
            yield SourceItem(
                source_collection=folder_name,
                source_id=item_id,
                raw_payload=raw,
                content_type="message/rfc822",
                version_token=getattr(msg, "changekey", "") or "",
                item_key=getattr(msg, "message_id", None) or item_id,
                metadata={
                    "ews_item_id": item_id,
                    "received": str(getattr(msg, "datetime_received", "") or ""),
                },
            )

    def _iter_calendar(self, account, resume_from: str | None) -> Iterator[SourceItem]:
        for item in account.calendar.all().order_by("start"):
            item_id = item.id
            if resume_from and item_id <= resume_from:
                continue
            try:
                raw = _calendar_item_to_ical(item)
            except Exception:
                raw = b""
            yield SourceItem(
                source_collection="Calendar",
                source_id=item_id,
                raw_payload=raw,
                content_type="text/calendar",
                version_token=getattr(item, "changekey", "") or "",
                item_key=getattr(item, "uid", None) or item_id,
                metadata={"ews_item_id": item_id},
            )

    def _iter_contacts(self, account, resume_from: str | None) -> Iterator[SourceItem]:
        for item in account.contacts.all().order_by("display_name"):
            item_id = item.id
            if resume_from and item_id <= resume_from:
                continue
            try:
                raw = _contact_to_vcard(item)
            except Exception:
                raw = b""
            yield SourceItem(
                source_collection="Contacts",
                source_id=item_id,
                raw_payload=raw,
                content_type="text/vcard",
                version_token=getattr(item, "changekey", "") or "",
                item_key=item_id,
                metadata={
                    "display_name": str(getattr(item, "display_name", "") or ""),
                    "ews_item_id": item_id,
                },
            )

    def _iter_tasks(self, account, resume_from: str | None) -> Iterator[SourceItem]:
        for item in account.tasks.all().order_by("subject"):
            item_id = item.id
            if resume_from and item_id <= resume_from:
                continue
            try:
                raw = _task_to_ical(item)
            except Exception:
                raw = b""
            yield SourceItem(
                source_collection="Tasks",
                source_id=item_id,
                raw_payload=raw,
                content_type="text/calendar",
                version_token=getattr(item, "changekey", "") or "",
                item_key=getattr(item, "uid", None) or item_id,
                metadata={"ews_item_id": item_id},
            )
