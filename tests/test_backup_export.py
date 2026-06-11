"""Tests for admin backup download and JSON export endpoints (Phase 12 cloud export)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _make_admin_client(db_path: Path) -> tuple[TestClient, object]:
    app = create_app(state_db_path=str(db_path))
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": "admin@example.com", "password": "AdminPass1!"})
    return client, resp.cookies


class TestBackupExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        self.store = SQLiteStateStore(self.db_path)
        self.store.create_user(
            email="admin@example.com",
            password_hash=hash_password("AdminPass1!"),
            is_admin=True,
            role="admin",
        )
        self.client, self.cookies = _make_admin_client(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # SQLite file download
    # ------------------------------------------------------------------

    def test_backup_download_returns_200(self):
        resp = self.client.get("/api/admin/backup/download", cookies=self.cookies)
        self.assertEqual(resp.status_code, 200)

    def test_backup_download_content_type_octet_stream(self):
        resp = self.client.get("/api/admin/backup/download", cookies=self.cookies)
        self.assertIn("application/octet-stream", resp.headers["content-type"])

    def test_backup_download_has_sqlite_magic(self):
        resp = self.client.get("/api/admin/backup/download", cookies=self.cookies)
        # SQLite files start with "SQLite format 3\x00"
        self.assertTrue(resp.content.startswith(b"SQLite format 3"))

    def test_backup_download_content_disposition(self):
        resp = self.client.get("/api/admin/backup/download", cookies=self.cookies)
        cd = resp.headers.get("content-disposition", "")
        self.assertIn(".db", cd)

    def test_backup_download_requires_admin(self):
        fresh = TestClient(self.client.app, raise_server_exceptions=True)
        resp = fresh.get("/api/admin/backup/download")
        self.assertIn(resp.status_code, {401, 403})

    # ------------------------------------------------------------------
    # JSON export
    # ------------------------------------------------------------------

    def test_export_returns_200(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        self.assertEqual(resp.status_code, 200)

    def test_export_returns_json(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        body = resp.json()
        self.assertIsInstance(body, dict)

    def test_export_has_schema_version(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        self.assertIn("schema_version", resp.json())
        self.assertEqual(resp.json()["schema_version"], 1)

    def test_export_has_expected_sections(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        body = resp.json()
        for key in ("jobs", "batches", "schedules", "webhooks", "organizations",
                    "users", "oidc_providers", "audit_events", "exported_at"):
            with self.subTest(key=key):
                self.assertIn(key, body)

    def test_export_users_no_password_hashes(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        for user in resp.json()["users"]:
            with self.subTest(email=user["email"]):
                self.assertNotIn("password_hash", user)

    def test_export_oidc_no_client_secrets(self):
        # Create an OIDC provider first
        self.store.create_oidc_provider(
            name="Test IdP",
            client_id="cid",
            client_secret="SUPERSECRET",
            issuer="https://idp.example.com",
        )
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        for provider in resp.json()["oidc_providers"]:
            self.assertNotIn("client_secret", provider)

    def test_export_includes_created_users(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        emails = {u["email"] for u in resp.json()["users"]}
        self.assertIn("admin@example.com", emails)

    def test_export_requires_admin(self):
        fresh = TestClient(self.client.app, raise_server_exceptions=True)
        resp = fresh.get("/api/admin/export")
        self.assertIn(resp.status_code, {401, 403})

    def test_export_content_disposition_json(self):
        resp = self.client.get("/api/admin/export", cookies=self.cookies)
        cd = resp.headers.get("content-disposition", "")
        self.assertIn(".json", cd)

    # ------------------------------------------------------------------
    # State store direct tests
    # ------------------------------------------------------------------

    def test_checkpoint_wal_does_not_raise(self):
        # Should complete without error on a fresh database
        self.store.checkpoint_wal()

    def test_export_state_returns_dict(self):
        data = self.store.export_state()
        self.assertIsInstance(data, dict)
        self.assertIn("jobs", data)
        self.assertIn("exported_at", data)

    def test_export_state_jobs_excludes_sensitive_plan(self):
        # Only columns explicitly selected; no raw plan JSON or credentials
        data = self.store.export_state()
        for job in data["jobs"]:
            self.assertNotIn("plan_json", job)

    def test_db_path_property(self):
        self.assertEqual(self.store.db_path, self.db_path)


if __name__ == "__main__":
    unittest.main()
