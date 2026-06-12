"""Tests for the EWS source connector (exchangelib mocked via sys.modules)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from groupware_migrator.connectors.ews import (
    _calendar_item_to_ical,
    _contact_to_vcard,
    _task_to_ical,
    EwsSourceConnector,
)
from groupware_migrator.models.domain import ConnectionConfig, WorkloadType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(host: str = "mail.corp.example.com") -> ConnectionConfig:
    return ConnectionConfig(
        host=host,
        port=443,
        username="user@corp.example.com",
        password="secret",
    )


def _make_ews_mod() -> tuple[MagicMock, MagicMock]:
    """Return (ews_module_mock, account_mock)."""
    account = MagicMock()
    account.inbox.total_count = 5
    account.calendar.total_count = 3
    account.contacts.total_count = 2
    account.tasks.total_count = 4

    ews = MagicMock()
    ews.Credentials.return_value = MagicMock()
    ews.Configuration.return_value = MagicMock()
    ews.Account.return_value = account
    ews.DELEGATE = "DELEGATE"
    return ews, account


def _connector(config: ConnectionConfig, workload: WorkloadType, ews_mod: MagicMock) -> EwsSourceConnector:
    conn = EwsSourceConnector(config, workload=workload)
    with patch.dict("sys.modules", {"exchangelib": ews_mod}):
        conn._get_account()
    return conn


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction(unittest.TestCase):

    def test_protocol_attribute(self):
        from groupware_migrator.models.domain import SourceProtocol
        self.assertEqual(EwsSourceConnector.protocol, SourceProtocol.EWS)

    def test_default_workload_is_mail(self):
        cfg = _make_config()
        conn = EwsSourceConnector(cfg)
        self.assertEqual(conn._workload, WorkloadType.MAIL)

    def test_workload_stored(self):
        cfg = _make_config()
        conn = EwsSourceConnector(cfg, workload=WorkloadType.CALENDAR)
        self.assertEqual(conn._workload, WorkloadType.CALENDAR)

    def test_missing_exchangelib_raises(self):
        cfg = _make_config()
        conn = EwsSourceConnector(cfg)
        with patch.dict("sys.modules", {"exchangelib": None}):
            # None in sys.modules causes ImportError on import
            with self.assertRaises((ImportError, Exception)):
                conn._get_account()

    def test_get_account_with_host_uses_configuration(self):
        cfg = _make_config(host="mail.corp.example.com")
        ews, account = _make_ews_mod()
        with patch.dict("sys.modules", {"exchangelib": ews}):
            conn = EwsSourceConnector(cfg)
            conn._get_account()
        ews.Configuration.assert_called_once()
        ews.Account.assert_called_once()

    def test_get_account_without_host_uses_autodiscover(self):
        cfg = _make_config(host="")
        ews, account = _make_ews_mod()
        with patch.dict("sys.modules", {"exchangelib": ews}):
            conn = EwsSourceConnector(cfg)
            conn._get_account()
        ews.Configuration.assert_not_called()
        call_kwargs = ews.Account.call_args[1]
        self.assertTrue(call_kwargs.get("autodiscover"))

    def test_account_cached(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        with patch.dict("sys.modules", {"exchangelib": ews}):
            conn = EwsSourceConnector(cfg)
            conn._get_account()
            conn._get_account()
        self.assertEqual(ews.Account.call_count, 1)


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

class TestValidate(unittest.TestCase):

    def test_validate_reads_inbox_count(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        conn.validate()
        _ = account.inbox.total_count


# ---------------------------------------------------------------------------
# list_collections
# ---------------------------------------------------------------------------

class TestListCollections(unittest.TestCase):

    def _setup_mail_folders(self, account):
        folder1 = MagicMock()
        folder1.name = "Inbox"
        folder1.folder_class = "IPF.Note"
        folder1.total_count = 10

        folder2 = MagicMock()
        folder2.name = "Sent Items"
        folder2.folder_class = "IPF.Note"
        folder2.total_count = 5

        folder3 = MagicMock()
        folder3.name = "Calendar"
        folder3.folder_class = "IPF.Appointment"  # should be excluded
        folder3.total_count = 3

        account.root.walk.return_value = [folder1, folder2, folder3]

    def test_mail_lists_ipf_note_folders(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        self._setup_mail_folders(account)
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        result = conn.list_collections()
        names = [s.name for s in result]
        self.assertIn("Inbox", names)
        self.assertIn("Sent Items", names)
        self.assertNotIn("Calendar", names)

    def test_mail_collections_have_item_counts(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        self._setup_mail_folders(account)
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        result = conn.list_collections()
        inbox = next(s for s in result if s.name == "Inbox")
        self.assertEqual(inbox.estimated_items, 10)

    def test_calendar_returns_single_collection(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        conn = _connector(cfg, WorkloadType.CALENDAR, ews)
        result = conn.list_collections()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "Calendar")
        self.assertEqual(result[0].estimated_items, 3)

    def test_contacts_returns_single_collection(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        conn = _connector(cfg, WorkloadType.CONTACTS, ews)
        result = conn.list_collections()
        self.assertEqual(result[0].name, "Contacts")
        self.assertEqual(result[0].estimated_items, 2)

    def test_tasks_returns_single_collection(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        conn = _connector(cfg, WorkloadType.TASKS, ews)
        result = conn.list_collections()
        self.assertEqual(result[0].name, "Tasks")
        self.assertEqual(result[0].estimated_items, 4)

    def test_mail_fallback_on_walk_error(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.root.walk.side_effect = Exception("EWS error")
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        result = conn.list_collections()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "Inbox")

    def test_list_mailboxes_delegates_to_list_collections(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        conn = _connector(cfg, WorkloadType.CALENDAR, ews)
        self.assertEqual(conn.list_mailboxes(), conn.list_collections())


# ---------------------------------------------------------------------------
# iter_items — mail
# ---------------------------------------------------------------------------

class TestIterMail(unittest.TestCase):

    def _setup_folder(self, account, messages):
        folder = MagicMock()
        folder.name = "Inbox"
        folder.folder_class = "IPF.Note"
        folder.total_count = len(messages)
        folder.all.return_value.order_by.return_value.only.return_value = messages
        account.root.walk.return_value = [folder]
        return folder

    def _make_msg(self, item_id: str, mime: bytes = b"raw mime") -> MagicMock:
        msg = MagicMock()
        msg.id = item_id
        msg.changekey = "ck-" + item_id
        msg.message_id = f"<{item_id}@example.com>"
        msg.datetime_received = "2024-01-01T10:00:00Z"
        msg.mime_content = mime
        return msg

    def test_iter_items_mail_yields_source_items(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        messages = [self._make_msg("id1"), self._make_msg("id2")]
        self._setup_folder(account, messages)
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        results = list(conn.iter_items("Inbox"))
        self.assertEqual(len(results), 2)

    def test_iter_items_mail_content_type(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        self._setup_folder(account, [self._make_msg("id1")])
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        item = list(conn.iter_items("Inbox"))[0]
        self.assertEqual(item.content_type, "message/rfc822")

    def test_iter_items_mail_raw_payload(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        self._setup_folder(account, [self._make_msg("id1", mime=b"MIME data")])
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        item = list(conn.iter_items("Inbox"))[0]
        self.assertEqual(item.raw_payload, b"MIME data")

    def test_iter_items_mail_resume_from(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        self._setup_folder(account, [self._make_msg("id1"), self._make_msg("id2")])
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        results = list(conn.iter_items("Inbox", resume_from="id1"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_id, "id2")

    def test_iter_items_str_mime_encoded(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        msg = self._make_msg("id1")
        msg.mime_content = "string mime"
        self._setup_folder(account, [msg])
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        item = list(conn.iter_items("Inbox"))[0]
        self.assertIsInstance(item.raw_payload, bytes)

    def test_iter_messages_delegates_to_iter_items(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        self._setup_folder(account, [self._make_msg("id1")])
        conn = _connector(cfg, WorkloadType.MAIL, ews)
        self.assertEqual(
            list(conn.iter_messages("Inbox")),
            list(conn.iter_items("Inbox")),
        )


# ---------------------------------------------------------------------------
# iter_items — calendar
# ---------------------------------------------------------------------------

class TestIterCalendar(unittest.TestCase):

    def _make_cal_item(self, item_id: str, subject: str = "Meeting") -> MagicMock:
        item = MagicMock()
        item.id = item_id
        item.changekey = "ck"
        item.uid = f"uid-{item_id}"
        item.subject = subject
        item.start = MagicMock()
        item.start.strftime = lambda fmt: "20240101T090000Z"
        item.start.date.return_value.strftime = lambda fmt: "20240101"
        item.end = MagicMock()
        item.end.strftime = lambda fmt: "20240101T100000Z"
        item.end.date.return_value.strftime = lambda fmt: "20240101"
        item.is_all_day = False
        item.location = "Room A"
        item.body = "Agenda"
        item.organizer = None
        item.required_attendees = []
        item.optional_attendees = []
        return item

    def test_iter_calendar_yields_ical(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.calendar.all.return_value.order_by.return_value = [self._make_cal_item("ev1")]
        conn = _connector(cfg, WorkloadType.CALENDAR, ews)
        results = list(conn.iter_items("Calendar"))
        self.assertEqual(len(results), 1)
        self.assertIn(b"BEGIN:VCALENDAR", results[0].raw_payload)
        self.assertIn(b"BEGIN:VEVENT", results[0].raw_payload)

    def test_iter_calendar_content_type(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.calendar.all.return_value.order_by.return_value = [self._make_cal_item("ev1")]
        conn = _connector(cfg, WorkloadType.CALENDAR, ews)
        item = list(conn.iter_items("Calendar"))[0]
        self.assertEqual(item.content_type, "text/calendar")

    def test_iter_calendar_resume_from(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.calendar.all.return_value.order_by.return_value = [
            self._make_cal_item("ev1"), self._make_cal_item("ev2")
        ]
        conn = _connector(cfg, WorkloadType.CALENDAR, ews)
        results = list(conn.iter_items("Calendar", resume_from="ev1"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_id, "ev2")


# ---------------------------------------------------------------------------
# iter_items — contacts
# ---------------------------------------------------------------------------

class TestIterContacts(unittest.TestCase):

    def _make_contact(self, item_id: str, given: str = "John", surname: str = "Doe") -> MagicMock:
        item = MagicMock()
        item.id = item_id
        item.changekey = "ck"
        item.given_name = given
        item.surname = surname
        item.display_name = f"{given} {surname}"
        item.company_name = "ACME"
        item.job_title = "Engineer"
        item.email_addresses = []
        item.phone_numbers = []
        item.physical_addresses = []
        return item

    def test_iter_contacts_yields_vcard(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.contacts.all.return_value.order_by.return_value = [self._make_contact("c1")]
        conn = _connector(cfg, WorkloadType.CONTACTS, ews)
        results = list(conn.iter_items("Contacts"))
        self.assertEqual(len(results), 1)
        self.assertIn(b"BEGIN:VCARD", results[0].raw_payload)
        self.assertIn(b"END:VCARD", results[0].raw_payload)

    def test_iter_contacts_content_type(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.contacts.all.return_value.order_by.return_value = [self._make_contact("c1")]
        conn = _connector(cfg, WorkloadType.CONTACTS, ews)
        item = list(conn.iter_items("Contacts"))[0]
        self.assertEqual(item.content_type, "text/vcard")


# ---------------------------------------------------------------------------
# iter_items — tasks
# ---------------------------------------------------------------------------

class TestIterTasks(unittest.TestCase):

    def _make_task(self, item_id: str, subject: str = "Do something") -> MagicMock:
        item = MagicMock()
        item.id = item_id
        item.changekey = "ck"
        item.uid = f"uid-{item_id}"
        item.subject = subject
        item.body = "Details"
        item.due_date = None
        item.start_date = None
        item.status = "NotStarted"
        item.percent_complete = 0
        return item

    def test_iter_tasks_yields_vtodo(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.tasks.all.return_value.order_by.return_value = [self._make_task("t1")]
        conn = _connector(cfg, WorkloadType.TASKS, ews)
        results = list(conn.iter_items("Tasks"))
        self.assertEqual(len(results), 1)
        self.assertIn(b"BEGIN:VTODO", results[0].raw_payload)

    def test_iter_tasks_content_type(self):
        cfg = _make_config()
        ews, account = _make_ews_mod()
        account.tasks.all.return_value.order_by.return_value = [self._make_task("t1")]
        conn = _connector(cfg, WorkloadType.TASKS, ews)
        item = list(conn.iter_items("Tasks"))[0]
        self.assertEqual(item.content_type, "text/calendar")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

class TestCalendarItemToIcal(unittest.TestCase):

    def _make_item(self, **kwargs) -> MagicMock:
        item = MagicMock()
        item.uid = kwargs.get("uid", "test-uid")
        item.subject = kwargs.get("subject", "Stand-up")
        item.start = None
        item.end = None
        item.is_all_day = False
        item.location = kwargs.get("location", "")
        item.body = kwargs.get("body", "")
        item.organizer = None
        item.required_attendees = []
        item.optional_attendees = []
        return item

    def test_contains_vcalendar_and_vevent(self):
        raw = _calendar_item_to_ical(self._make_item())
        self.assertIn(b"BEGIN:VCALENDAR", raw)
        self.assertIn(b"BEGIN:VEVENT", raw)
        self.assertIn(b"END:VEVENT", raw)
        self.assertIn(b"END:VCALENDAR", raw)

    def test_uid_in_output(self):
        raw = _calendar_item_to_ical(self._make_item(uid="my-uid-123"))
        self.assertIn(b"UID:my-uid-123", raw)

    def test_subject_in_output(self):
        raw = _calendar_item_to_ical(self._make_item(subject="Team meeting"))
        self.assertIn(b"SUMMARY:Team meeting", raw)

    def test_location_in_output(self):
        raw = _calendar_item_to_ical(self._make_item(location="Conference Room B"))
        self.assertIn(b"LOCATION:Conference Room B", raw)

    def test_organizer_in_output(self):
        item = self._make_item()
        item.organizer = MagicMock()
        item.organizer.email_address = "boss@example.com"
        raw = _calendar_item_to_ical(item)
        self.assertIn(b"ORGANIZER:mailto:boss@example.com", raw)

    def test_attendee_in_output(self):
        item = self._make_item()
        attendee = MagicMock()
        attendee.mailbox.email_address = "alice@example.com"
        item.required_attendees = [attendee]
        raw = _calendar_item_to_ical(item)
        self.assertIn(b"ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:alice@example.com", raw)

    def test_auto_uid_when_none(self):
        item = self._make_item()
        item.uid = None
        raw = _calendar_item_to_ical(item)
        self.assertIn(b"UID:", raw)


class TestTaskToIcal(unittest.TestCase):

    def _make_task(self, **kwargs) -> MagicMock:
        item = MagicMock()
        item.uid = kwargs.get("uid", "task-uid")
        item.subject = kwargs.get("subject", "Fix bug")
        item.body = kwargs.get("body", "")
        item.due_date = None
        item.start_date = None
        item.status = kwargs.get("status", "NotStarted")
        item.percent_complete = kwargs.get("percent", 0)
        return item

    def test_contains_vtodo(self):
        raw = _task_to_ical(self._make_task())
        self.assertIn(b"BEGIN:VTODO", raw)
        self.assertIn(b"END:VTODO", raw)

    def test_status_mapping(self):
        item = self._make_task(status="Completed")
        raw = _task_to_ical(item)
        self.assertIn(b"STATUS:COMPLETED", raw)

    def test_in_progress_status(self):
        raw = _task_to_ical(self._make_task(status="InProgress"))
        self.assertIn(b"STATUS:IN-PROCESS", raw)

    def test_percent_complete(self):
        raw = _task_to_ical(self._make_task(percent=75))
        self.assertIn(b"PERCENT-COMPLETE:75", raw)


class TestContactToVcard(unittest.TestCase):

    def _make_contact(self, **kwargs) -> MagicMock:
        item = MagicMock()
        item.given_name = kwargs.get("given", "Jane")
        item.surname = kwargs.get("surname", "Smith")
        item.display_name = kwargs.get("display", "Jane Smith")
        item.company_name = kwargs.get("company", "")
        item.job_title = kwargs.get("title", "")
        item.email_addresses = []
        item.phone_numbers = []
        item.physical_addresses = []
        return item

    def test_contains_vcard(self):
        raw = _contact_to_vcard(self._make_contact())
        self.assertIn(b"BEGIN:VCARD", raw)
        self.assertIn(b"END:VCARD", raw)

    def test_version_30(self):
        raw = _contact_to_vcard(self._make_contact())
        self.assertIn(b"VERSION:3.0", raw)

    def test_fn_and_n_fields(self):
        raw = _contact_to_vcard(self._make_contact(given="Jane", surname="Smith", display="Jane Smith"))
        self.assertIn(b"FN:Jane Smith", raw)
        self.assertIn(b"N:Smith;Jane;;;", raw)

    def test_email_in_output(self):
        item = self._make_contact()
        ea = MagicMock()
        ea.email = "jane@example.com"
        ea.label = "EmailAddress1"
        item.email_addresses = [ea]
        raw = _contact_to_vcard(item)
        self.assertIn(b"EMAIL;TYPE=WORK:jane@example.com", raw)

    def test_phone_in_output(self):
        item = self._make_contact()
        pn = MagicMock()
        pn.phone_number = "+1-555-0100"
        pn.label = "MobilePhone"
        item.phone_numbers = [pn]
        raw = _contact_to_vcard(item)
        self.assertIn(b"TEL;TYPE=CELL:+1-555-0100", raw)

    def test_company_and_title(self):
        raw = _contact_to_vcard(self._make_contact(company="ACME Corp", title="CTO"))
        self.assertIn(b"ORG:ACME Corp", raw)
        self.assertIn(b"TITLE:CTO", raw)


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------

class TestDomainValidation(unittest.TestCase):

    def test_ews_allowed_for_mail_workload(self):
        from groupware_migrator.models.domain import _validate_workload_protocols, WorkloadType, SourceProtocol, DestinationProtocol
        _validate_workload_protocols(
            workload=WorkloadType.MAIL,
            source_protocol=SourceProtocol.EWS,
            destination_protocol=DestinationProtocol.IMAP,
        )

    def test_ews_allowed_for_calendar_workload(self):
        from groupware_migrator.models.domain import _validate_workload_protocols, WorkloadType, SourceProtocol, DestinationProtocol
        _validate_workload_protocols(
            workload=WorkloadType.CALENDAR,
            source_protocol=SourceProtocol.EWS,
            destination_protocol=DestinationProtocol.CALDAV,
        )

    def test_ews_allowed_for_contacts_workload(self):
        from groupware_migrator.models.domain import _validate_workload_protocols, WorkloadType, SourceProtocol, DestinationProtocol
        _validate_workload_protocols(
            workload=WorkloadType.CONTACTS,
            source_protocol=SourceProtocol.EWS,
            destination_protocol=DestinationProtocol.CARDDAV,
        )

    def test_ews_allowed_for_tasks_workload(self):
        from groupware_migrator.models.domain import _validate_workload_protocols, WorkloadType, SourceProtocol, DestinationProtocol
        _validate_workload_protocols(
            workload=WorkloadType.TASKS,
            source_protocol=SourceProtocol.EWS,
            destination_protocol=DestinationProtocol.CALDAV,
        )

    def test_ews_in_source_protocol_enum(self):
        from groupware_migrator.models.domain import SourceProtocol
        self.assertEqual(SourceProtocol.EWS, "ews")

    def test_factory_routes_ews_to_connector(self):
        from groupware_migrator.connectors.factory import create_source_connector
        from groupware_migrator.models.domain import MigrationRequest
        request = MigrationRequest.from_dict({
            "source": {
                "protocol": "ews",
                "connection": {"host": "mail.corp.example.com", "username": "user@corp.example.com", "password": "pass"},
            },
            "destination": {
                "protocol": "imap",
                "connection": {"host": "dest.example.com", "username": "user", "password": "pass"},
            },
            "workload": "mail",
        })
        connector = create_source_connector(request)
        self.assertIsInstance(connector, EwsSourceConnector)
        self.assertEqual(connector._workload, WorkloadType.MAIL)

    def test_factory_passes_workload_to_ews_connector(self):
        from groupware_migrator.connectors.factory import create_source_connector
        from groupware_migrator.models.domain import MigrationRequest
        request = MigrationRequest.from_dict({
            "source": {
                "protocol": "ews",
                "connection": {"host": "mail.corp.example.com", "username": "user@corp.example.com", "password": "pass"},
            },
            "destination": {
                "protocol": "caldav",
                "connection": {"host": "cal.example.com", "username": "user", "password": "pass"},
            },
            "workload": "calendar",
        })
        connector = create_source_connector(request)
        self.assertIsInstance(connector, EwsSourceConnector)
        self.assertEqual(connector._workload, WorkloadType.CALENDAR)


if __name__ == "__main__":
    unittest.main()
