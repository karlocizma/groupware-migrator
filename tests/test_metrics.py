import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.api.routers.metrics_router import _build_metrics_text
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _make_stats(**overrides) -> dict:
    base = {
        "users_total": 3,
        "jobs_total": 10,
        "jobs_running": 1,
        "jobs_completed": 7,
        "jobs_failed": 1,
        "jobs_cancelled": 1,
        "jobs_last_7d": 5,
        "success_rate_7d_pct": 80,
        "jobs_last_30d": 10,
        "success_rate_30d_pct": 70,
        "items_migrated_total": 5000,
        "items_skipped_total": 200,
        "items_failed_total": 15,
        "scheduled_jobs_total": 2,
        "batches_total": 3,
    }
    base.update(overrides)
    return base


class TestBuildMetricsText(unittest.TestCase):
    def setUp(self):
        self.text = _build_metrics_text(_make_stats())

    def test_contains_build_info(self):
        self.assertIn("groupware_migrator_build_info", self.text)

    def test_jobs_completed_label(self):
        self.assertIn('status="completed"} 7', self.text)

    def test_jobs_failed_label(self):
        self.assertIn('status="failed"} 1', self.text)

    def test_jobs_running_label(self):
        self.assertIn('status="running"} 1', self.text)

    def test_jobs_cancelled_label(self):
        self.assertIn('status="cancelled"} 1', self.text)

    def test_active_jobs_gauge(self):
        self.assertIn("groupware_migrator_active_jobs 1", self.text)

    def test_items_migrated_counter(self):
        self.assertIn("groupware_migrator_items_migrated_total 5000", self.text)

    def test_items_skipped_counter(self):
        self.assertIn("groupware_migrator_items_skipped_total 200", self.text)

    def test_items_failed_counter(self):
        self.assertIn("groupware_migrator_items_failed_total 15", self.text)

    def test_users_gauge(self):
        self.assertIn("groupware_migrator_users_total 3", self.text)

    def test_scheduled_jobs_gauge(self):
        self.assertIn("groupware_migrator_scheduled_jobs_total 2", self.text)

    def test_batches_gauge(self):
        self.assertIn("groupware_migrator_batches_total 3", self.text)

    def test_type_annotations_present(self):
        self.assertIn("# TYPE groupware_migrator_jobs_total gauge", self.text)
        self.assertIn("# TYPE groupware_migrator_items_migrated_total counter", self.text)

    def test_help_annotations_present(self):
        self.assertIn("# HELP groupware_migrator_jobs_total", self.text)

    def test_ends_with_newline(self):
        self.assertTrue(self.text.endswith("\n"))


def _make_client(db_path: Path, email: str, password: str) -> tuple[TestClient, object]:
    """Return (client, cookies) for a logged-in user on a fresh TestClient."""
    app = create_app(state_db_path=str(db_path))
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    return client, resp.cookies


class TestMetricsEndpoint(unittest.TestCase):
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
        self.client, self.cookies = _make_client(
            self.db_path, "admin@example.com", "AdminPass1!"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_metrics_returns_200_for_admin(self):
        resp = self.client.get("/metrics", cookies=self.cookies)
        self.assertEqual(resp.status_code, 200)

    def test_metrics_content_type_is_text(self):
        resp = self.client.get("/metrics", cookies=self.cookies)
        self.assertIn("text/plain", resp.headers["content-type"])

    def test_metrics_contains_prometheus_format(self):
        resp = self.client.get("/metrics", cookies=self.cookies)
        self.assertIn("# HELP", resp.text)
        self.assertIn("# TYPE", resp.text)
        self.assertIn("groupware_migrator_", resp.text)

    def test_metrics_requires_auth(self):
        # Fresh client with no login
        app = create_app(state_db_path=str(self.db_path))
        fresh = TestClient(app, raise_server_exceptions=True)
        resp = fresh.get("/metrics")
        self.assertIn(resp.status_code, {401, 403})

    def test_metrics_requires_admin(self):
        self.store.create_user(
            email="viewer@example.com",
            password_hash=hash_password("ViewerPass1!"),
            is_admin=False,
            role="viewer",
        )
        viewer_client, viewer_cookies = _make_client(
            self.db_path, "viewer@example.com", "ViewerPass1!"
        )
        resp = viewer_client.get("/metrics", cookies=viewer_cookies)
        self.assertIn(resp.status_code, {401, 403})


class TestHealthReady(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "state.db"
        SQLiteStateStore(db_path).create_user(
            email="admin@example.com",
            password_hash=hash_password("AdminPass1!"),
            is_admin=True,
            role="admin",
        )
        app = create_app(state_db_path=str(db_path))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_ready_returns_200(self):
        resp = self.client.get("/health/ready")
        self.assertEqual(resp.status_code, 200)

    def test_health_ready_includes_db_latency(self):
        resp = self.client.get("/health/ready")
        body = resp.json()
        self.assertIn("db_latency_ms", body)
        self.assertIsInstance(body["db_latency_ms"], float)

    def test_health_ready_includes_active_jobs(self):
        resp = self.client.get("/health/ready")
        body = resp.json()
        self.assertIn("active_jobs", body)
        self.assertEqual(body["active_jobs"], 0)

    def test_health_live_still_200(self):
        self.assertEqual(self.client.get("/health/live").status_code, 200)


if __name__ == "__main__":
    unittest.main()
