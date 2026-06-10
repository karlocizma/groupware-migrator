"""Tests for Phase 5 (Scheduling, Webhooks, Priority) and Phase 7 (TOTP, RBAC, Orgs, Vault)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from groupware_migrator.engine.cron import cron_next, parse_interval_seconds
from groupware_migrator.engine.state import SQLiteStateStore, hash_password
from groupware_migrator.engine.vault import decrypt, encrypt, is_encrypted, vault_enabled
from groupware_migrator.models import MigrationPlan, MigrationRequest
from groupware_migrator.models.domain import (
    ConnectionConfig,
    DestinationEndpoint,
    DestinationProtocol,
    MigrationOptions,
    SourceEndpoint,
    SourceProtocol,
    WorkloadType,
)


def _make_request() -> MigrationRequest:
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


class TestCronParser(unittest.TestCase):
    def _next(self, expr: str, after_iso: str) -> str:
        from datetime import datetime, timezone
        after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
        return cron_next(expr, after=after).strftime("%Y-%m-%d %H:%M")

    def test_every_minute(self):
        result = self._next("* * * * *", "2024-01-15T10:30:00")
        self.assertEqual(result, "2024-01-15 10:31")

    def test_daily_at_2am(self):
        result = self._next("0 2 * * *", "2024-01-15T10:30:00")
        self.assertEqual(result, "2024-01-16 02:00")

    def test_every_6_hours(self):
        result = self._next("0 */6 * * *", "2024-01-15T04:00:00")
        self.assertEqual(result, "2024-01-15 06:00")

    def test_weekly_sunday(self):
        result = self._next("0 0 * * 0", "2024-01-15T10:00:00")  # Monday
        self.assertEqual(result, "2024-01-21 00:00")

    def test_monthly_first(self):
        result = self._next("0 8 1 * *", "2024-01-15T10:00:00")
        self.assertEqual(result, "2024-02-01 08:00")

    def test_invalid_fields_raises(self):
        with self.assertRaises(ValueError):
            from datetime import datetime, timezone
            cron_next("* * *", after=datetime.now(timezone.utc))

    def test_parse_interval_hours(self):
        self.assertEqual(parse_interval_seconds("6h"), 6 * 3600)

    def test_parse_interval_days(self):
        self.assertEqual(parse_interval_seconds("1d"), 86400)

    def test_parse_interval_minutes(self):
        self.assertEqual(parse_interval_seconds("30m"), 1800)

    def test_parse_interval_invalid(self):
        with self.assertRaises(ValueError):
            parse_interval_seconds("unknown")


class TestScheduledJobs(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        self._user_id = self._store.create_user(
            email="u@x.com", password_hash=hash_password("pw")
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_and_get_schedule(self):
        sched_id = self._store.create_schedule(
            name="nightly",
            schedule_type="cron",
            schedule_expr="0 2 * * *",
            request_json='{"workload":"mail"}',
            next_run_at="2024-01-16T02:00:00+00:00",
            user_id=self._user_id,
        )
        row = self._store.get_schedule(sched_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "nightly")
        self.assertEqual(row["schedule_expr"], "0 2 * * *")
        self.assertEqual(row["is_active"], 1)

    def test_list_schedules_filtered_by_user(self):
        uid2 = self._store.create_user(email="u2@x.com", password_hash=hash_password("pw"))
        self._store.create_schedule(
            name="s1", schedule_type="cron", schedule_expr="* * * * *",
            request_json="{}", next_run_at="2024-01-01T00:00:00+00:00", user_id=self._user_id,
        )
        self._store.create_schedule(
            name="s2", schedule_type="cron", schedule_expr="* * * * *",
            request_json="{}", next_run_at="2024-01-01T00:00:00+00:00", user_id=uid2,
        )
        rows = self._store.list_schedules(user_id=self._user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "s1")

    def test_update_schedule_pause_resume(self):
        sched_id = self._store.create_schedule(
            name="x", schedule_type="cron", schedule_expr="* * * * *",
            request_json="{}", next_run_at="2024-01-01T00:00:00+00:00",
        )
        self._store.update_schedule(sched_id, is_active=False)
        self.assertEqual(self._store.get_schedule(sched_id)["is_active"], 0)
        self._store.update_schedule(sched_id, is_active=True)
        self.assertEqual(self._store.get_schedule(sched_id)["is_active"], 1)

    def test_list_due_schedules(self):
        sched_id = self._store.create_schedule(
            name="due",
            schedule_type="cron",
            schedule_expr="* * * * *",
            request_json="{}",
            next_run_at="2020-01-01T00:00:00+00:00",  # past
        )
        due = self._store.list_due_schedules(before="2025-01-01T00:00:00+00:00")
        ids = [d["id"] for d in due]
        self.assertIn(sched_id, ids)

    def test_update_schedule_after_fire(self):
        sched_id = self._store.create_schedule(
            name="x", schedule_type="cron", schedule_expr="* * * * *",
            request_json="{}", next_run_at="2020-01-01T00:00:00+00:00",
        )
        req = _make_request()
        job_id = self._store.create_job(req, MigrationPlan())
        self._store.update_schedule_after_fire(
            schedule_id=sched_id, job_id=job_id, next_run_at="2025-01-01T00:00:00+00:00"
        )
        row = self._store.get_schedule(sched_id)
        self.assertEqual(row["last_run_job_id"], job_id)
        self.assertEqual(row["next_run_at"], "2025-01-01T00:00:00+00:00")

    def test_delete_schedule(self):
        sched_id = self._store.create_schedule(
            name="del", schedule_type="cron", schedule_expr="* * * * *",
            request_json="{}", next_run_at="2025-01-01T00:00:00+00:00",
        )
        self.assertTrue(self._store.delete_schedule(sched_id))
        self.assertIsNone(self._store.get_schedule(sched_id))


class TestWebhooks(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        self._user_id = self._store.create_user(
            email="u@x.com", password_hash=hash_password("pw")
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_and_get_webhook(self):
        wh_id = self._store.create_webhook(
            user_id=self._user_id,
            label="test",
            url="https://hooks.example.com/abc",
            secret="s3cr3t",
            events=["job.completed"],
        )
        hook = self._store.get_webhook(wh_id)
        self.assertIsNotNone(hook)
        self.assertEqual(hook["url"], "https://hooks.example.com/abc")
        self.assertEqual(hook["events"], ["job.completed"])

    def test_list_webhooks_for_event(self):
        wh1 = self._store.create_webhook(
            user_id=self._user_id, label="", url="https://a.com/",
            secret="x", events=["job.completed"],
        )
        wh2 = self._store.create_webhook(
            user_id=self._user_id, label="", url="https://b.com/",
            secret="x", events=["job.failed"],
        )
        completed_hooks = self._store.list_webhooks_for_event(event_type="job.completed", user_id=self._user_id)
        ids = [h["id"] for h in completed_hooks]
        self.assertIn(wh1, ids)
        self.assertNotIn(wh2, ids)

    def test_append_and_list_deliveries(self):
        wh_id = self._store.create_webhook(
            user_id=self._user_id, label="", url="https://a.com/",
            secret="x", events=["job.completed"],
        )
        self._store.append_webhook_delivery(
            webhook_id=wh_id, event_type="job.completed",
            payload_json='{"event":"job.completed"}',
            response_status=200, error=None, attempt=1,
        )
        deliveries = self._store.list_webhook_deliveries(wh_id)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["response_status"], 200)

    def test_delete_webhook(self):
        wh_id = self._store.create_webhook(
            user_id=self._user_id, label="", url="https://a.com/", secret="x",
        )
        self.assertTrue(self._store.delete_webhook(wh_id))
        self.assertIsNone(self._store.get_webhook(wh_id))

    def test_webhook_delivery_hmac_signature(self):
        import hashlib
        import hmac as _hmac

        secret = "test-secret"
        body = b'{"event":"job.completed"}'
        sig = "sha256=" + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(sig.startswith("sha256="))
        self.assertEqual(len(sig), 7 + 64)  # "sha256=" + 64 hex chars


class TestJobPriority(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_priority_is_normal(self):
        req = _make_request()
        job_id = self._store.create_job(req, MigrationPlan())
        row = self._store.get_job(job_id)
        self.assertEqual(row.get("priority", "normal"), "normal")

    def test_explicit_priority_stored(self):
        req = _make_request()
        job_id = self._store.create_job(req, MigrationPlan(), priority="high")
        row = self._store.get_job(job_id)
        self.assertEqual(row["priority"], "high")


class TestTOTP(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        self._user_id = self._store.create_user(
            email="u@x.com", password_hash=hash_password("pw")
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_and_enable_totp(self):
        import pyotp
        secret = pyotp.random_base32()
        codes = ["AAAAAAAA", "BBBBBBBB"]
        self._store.set_totp_secret(self._user_id, secret=secret, recovery_codes=codes)
        user = self._store.get_user_by_id(self._user_id)
        self.assertFalse(bool(user["totp_enabled"]))
        self._store.enable_totp(self._user_id)
        user = self._store.get_user_by_id(self._user_id)
        self.assertTrue(bool(user["totp_enabled"]))

    def test_disable_totp(self):
        import pyotp
        self._store.set_totp_secret(self._user_id, secret=pyotp.random_base32(), recovery_codes=[])
        self._store.enable_totp(self._user_id)
        self._store.disable_totp(self._user_id)
        user = self._store.get_user_by_id(self._user_id)
        self.assertFalse(bool(user["totp_enabled"]))
        self.assertIsNone(user["totp_secret"])

    def test_totp_verify(self):
        import pyotp
        secret = pyotp.random_base32()
        self._store.set_totp_secret(self._user_id, secret=secret, recovery_codes=["DEADBEEF"])
        self._store.enable_totp(self._user_id)
        totp = pyotp.TOTP(secret)
        current_code = totp.now()
        self.assertTrue(totp.verify(current_code, valid_window=1))

    def test_consume_recovery_code(self):
        import pyotp
        secret = pyotp.random_base32()
        recovery = ["CODE0001", "CODE0002"]
        self._store.set_totp_secret(self._user_id, secret=secret, recovery_codes=recovery)
        self.assertTrue(self._store.consume_totp_recovery_code(self._user_id, "CODE0001"))
        self.assertFalse(self._store.consume_totp_recovery_code(self._user_id, "CODE0001"))
        self.assertTrue(self._store.consume_totp_recovery_code(self._user_id, "CODE0002"))


class TestRBAC(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_role_is_operator(self):
        uid = self._store.create_user(email="u@x.com", password_hash=hash_password("pw"))
        user = self._store.get_user_by_id(uid)
        self.assertEqual(user.get("role", "operator"), "operator")

    def test_create_user_with_role(self):
        uid = self._store.create_user(
            email="admin@x.com", password_hash=hash_password("pw"), role="admin"
        )
        user = self._store.get_user_by_id(uid)
        self.assertEqual(user["role"], "admin")

    def test_update_user_role(self):
        uid = self._store.create_user(email="u@x.com", password_hash=hash_password("pw"))
        self._store.update_user(uid, role="super_admin")
        user = self._store.get_user_by_id(uid)
        self.assertEqual(user["role"], "super_admin")
        self.assertEqual(user["is_admin"], 1)

    def test_viewer_role_keeps_is_admin_false(self):
        uid = self._store.create_user(email="u@x.com", password_hash=hash_password("pw"))
        self._store.update_user(uid, role="viewer")
        user = self._store.get_user_by_id(uid)
        self.assertEqual(user["role"], "viewer")
        self.assertEqual(user["is_admin"], 0)

    def test_require_admin_dep_respects_is_admin_flag(self):
        from groupware_migrator.api.auth import require_admin
        from fastapi import HTTPException
        user = {"sub": "u1", "email": "u@x.com", "is_admin": True, "role": "admin"}
        result = require_admin(user)
        self.assertEqual(result["sub"], "u1")

    def test_require_admin_dep_rejects_non_admin(self):
        from fastapi import HTTPException
        from groupware_migrator.api.auth import require_admin
        user = {"sub": "u1", "email": "u@x.com", "is_admin": False, "role": "operator"}
        with self.assertRaises(HTTPException) as ctx:
            require_admin(user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_operator_allows_operator(self):
        from groupware_migrator.api.auth import require_operator
        user = {"sub": "u1", "email": "u@x.com", "is_admin": False, "role": "operator"}
        result = require_operator(user)
        self.assertEqual(result["sub"], "u1")

    def test_require_operator_rejects_viewer(self):
        from fastapi import HTTPException
        from groupware_migrator.api.auth import require_operator
        user = {"sub": "u1", "email": "u@x.com", "is_admin": False, "role": "viewer"}
        with self.assertRaises(HTTPException) as ctx:
            require_operator(user)
        self.assertEqual(ctx.exception.status_code, 403)


class TestOrganizations(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._store = SQLiteStateStore(Path(self._tmp.name) / "state.db")
        self._user_id = self._store.create_user(
            email="owner@x.com", password_hash=hash_password("pw"), is_admin=True
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_org(self):
        org_id = self._store.create_org(name="Acme", slug="acme", created_by=self._user_id)
        org = self._store.get_org(org_id)
        self.assertIsNotNone(org)
        self.assertEqual(org["name"], "Acme")
        self.assertEqual(org["slug"], "acme")

    def test_creator_is_owner(self):
        org_id = self._store.create_org(name="Beta", slug="beta", created_by=self._user_id)
        role = self._store.get_org_member_role(org_id, self._user_id)
        self.assertEqual(role, "owner")

    def test_add_and_remove_member(self):
        org_id = self._store.create_org(name="Gamma", slug="gamma", created_by=self._user_id)
        uid2 = self._store.create_user(email="u2@x.com", password_hash=hash_password("pw"))
        self._store.add_org_member(org_id, uid2, "member")
        self.assertEqual(self._store.get_org_member_role(org_id, uid2), "member")
        self._store.remove_org_member(org_id, uid2)
        self.assertIsNone(self._store.get_org_member_role(org_id, uid2))

    def test_list_org_members(self):
        org_id = self._store.create_org(name="Delta", slug="delta", created_by=self._user_id)
        uid2 = self._store.create_user(email="u2@x.com", password_hash=hash_password("pw"))
        self._store.add_org_member(org_id, uid2)
        members = self._store.list_org_members(org_id)
        member_ids = [m["id"] for m in members]
        self.assertIn(self._user_id, member_ids)
        self.assertIn(uid2, member_ids)

    def test_list_orgs_filtered_by_user(self):
        uid2 = self._store.create_user(email="u2@x.com", password_hash=hash_password("pw"))
        self._store.create_org(name="OwnerOrg", slug="owner-org", created_by=self._user_id)
        self._store.create_org(name="OtherOrg", slug="other-org", created_by=uid2)
        my_orgs = self._store.list_orgs(user_id=self._user_id)
        self.assertEqual(len(my_orgs), 1)
        self.assertEqual(my_orgs[0]["name"], "OwnerOrg")

    def test_get_org_by_slug(self):
        self._store.create_org(name="SlugTest", slug="slug-test", created_by=self._user_id)
        org = self._store.get_org_by_slug("slug-test")
        self.assertIsNotNone(org)
        self.assertEqual(org["name"], "SlugTest")

    def test_delete_org(self):
        org_id = self._store.create_org(name="Del", slug="del", created_by=self._user_id)
        self.assertTrue(self._store.delete_org(org_id))
        self.assertIsNone(self._store.get_org(org_id))


class TestCredentialVault(unittest.TestCase):
    def test_vault_disabled_returns_plaintext(self):
        result = encrypt("my-secret-password")
        self.assertEqual(result, "my-secret-password")
        self.assertFalse(is_encrypted(result))

    def test_vault_disabled_decrypt_returns_plaintext(self):
        result = decrypt("plain-text")
        self.assertEqual(result, "plain-text")

    def test_vault_not_enabled_by_default(self):
        self.assertFalse(vault_enabled())

    def test_vault_encrypt_decrypt(self):
        import base64
        import secrets as _s

        key = base64.urlsafe_b64encode(_s.token_bytes(32)).rstrip(b"=").decode()
        with patch.dict(os.environ, {"VAULT_KEY": key}):
            encrypted = encrypt("sensitive-password")
            self.assertTrue(is_encrypted(encrypted))
            self.assertTrue(encrypted.startswith("vault:"))
            decrypted = decrypt(encrypted)
            self.assertEqual(decrypted, "sensitive-password")

    def test_vault_decrypt_without_key_raises(self):
        with patch.dict(os.environ, {"VAULT_KEY": ""}):
            with self.assertRaises(ValueError):
                decrypt("vault:some-encrypted-blob")

    def test_vault_enabled_with_key(self):
        import base64
        import secrets as _s

        key = base64.urlsafe_b64encode(_s.token_bytes(32)).rstrip(b"=").decode()
        with patch.dict(os.environ, {"VAULT_KEY": key}):
            self.assertTrue(vault_enabled())


if __name__ == "__main__":
    unittest.main()
