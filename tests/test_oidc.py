"""Tests for OIDC/SSO phase — state HMAC, store CRUD, and HTTP endpoints."""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.engine.oidc import (
    IDP_PRESETS,
    OIDCProviderConfig,
    make_state,
    verify_state,
)
from groupware_migrator.engine.state import SQLiteStateStore, hash_password

_SECRET = "test-jwt-secret-not-real"

_PROVIDER_ROW = {
    "id": "abc123",
    "name": "Test IdP",
    "client_id": "client-id",
    "client_secret": "client-secret",
    "issuer": "https://idp.example.com",
    "discovery_url": "",
    "scope": "openid email profile",
    "admin_claim": "",
    "admin_claim_value": "",
    "created_at": "2026-01-01T00:00:00+00:00",
}

_FAKE_DISCOVERY = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "jwks_uri": "https://idp.example.com/jwks",
    "issuer": "https://idp.example.com",
}


# ---------------------------------------------------------------------------
# Unit tests — state HMAC
# ---------------------------------------------------------------------------

class TestMakeVerifyState(unittest.TestCase):
    def test_roundtrip(self):
        nonce, state = make_state(_SECRET)
        returned_nonce = verify_state(_SECRET, state)
        self.assertEqual(nonce, returned_nonce)

    def test_tampered_state_raises(self):
        _, state = make_state(_SECRET)
        with self.assertRaises(ValueError):
            verify_state(_SECRET, state + "X")

    def test_wrong_secret_raises(self):
        _, state = make_state(_SECRET)
        with self.assertRaises(ValueError):
            verify_state("wrong-secret", state)

    def test_malformed_state_raises(self):
        with self.assertRaises(ValueError):
            verify_state(_SECRET, "no-dot-here")

    def test_unique_nonces(self):
        nonces = {make_state(_SECRET)[0] for _ in range(10)}
        self.assertEqual(len(nonces), 10)


# ---------------------------------------------------------------------------
# Unit tests — IDP presets
# ---------------------------------------------------------------------------

class TestIdpPresets(unittest.TestCase):
    def test_presets_non_empty(self):
        self.assertGreater(len(IDP_PRESETS), 0)

    def test_each_preset_has_required_keys(self):
        for preset in IDP_PRESETS:
            with self.subTest(id=preset["id"]):
                self.assertIn("id", preset)
                self.assertIn("name", preset)
                self.assertIn("issuer_template", preset)
                self.assertIn("discovery_url_template", preset)

    def test_known_presets_present(self):
        ids = {p["id"] for p in IDP_PRESETS}
        self.assertIn("keycloak", ids)
        self.assertIn("okta", ids)
        self.assertIn("auth0", ids)
        self.assertIn("entra", ids)
        self.assertIn("google", ids)


# ---------------------------------------------------------------------------
# Unit tests — state store CRUD
# ---------------------------------------------------------------------------

class TestOIDCProviderStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStateStore(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_get(self):
        pid = self.store.create_oidc_provider(
            name="Test IdP",
            client_id="cid",
            client_secret="csec",
            issuer="https://idp.example.com",
        )
        row = self.store.get_oidc_provider(pid)
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Test IdP")
        self.assertEqual(row["client_id"], "cid")
        self.assertEqual(row["issuer"], "https://idp.example.com")
        self.assertEqual(row["scope"], "openid email profile")

    def test_list_providers(self):
        self.store.create_oidc_provider(
            name="P1", client_id="c1", client_secret="s1", issuer="https://p1.example.com"
        )
        self.store.create_oidc_provider(
            name="P2", client_id="c2", client_secret="s2", issuer="https://p2.example.com"
        )
        rows = self.store.list_oidc_providers()
        self.assertEqual(len(rows), 2)
        names = {r["name"] for r in rows}
        self.assertIn("P1", names)
        self.assertIn("P2", names)

    def test_delete_provider(self):
        pid = self.store.create_oidc_provider(
            name="Temp", client_id="c", client_secret="s", issuer="https://tmp.example.com"
        )
        self.assertTrue(self.store.delete_oidc_provider(pid))
        self.assertIsNone(self.store.get_oidc_provider(pid))

    def test_delete_nonexistent_returns_false(self):
        self.assertFalse(self.store.delete_oidc_provider("does-not-exist"))

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(self.store.get_oidc_provider("no-such-id"))

    def test_custom_fields_stored(self):
        pid = self.store.create_oidc_provider(
            name="P",
            client_id="c",
            client_secret="s",
            issuer="https://p.example.com",
            admin_claim="groups",
            admin_claim_value="admins",
            scope="openid email",
        )
        row = self.store.get_oidc_provider(pid)
        self.assertEqual(row["admin_claim"], "groups")
        self.assertEqual(row["admin_claim_value"], "admins")
        self.assertEqual(row["scope"], "openid email")


# ---------------------------------------------------------------------------
# Integration tests — HTTP endpoints
# ---------------------------------------------------------------------------

def _make_admin_client(db_path: Path) -> tuple[TestClient, object]:
    app = create_app(state_db_path=str(db_path))
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": "admin@example.com", "password": "AdminPass1!"})
    return client, resp.cookies


class TestOIDCAdminEndpoints(unittest.TestCase):
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

    def test_list_providers_empty(self):
        resp = self.client.get("/admin/oidc/providers", cookies=self.cookies)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_create_provider(self):
        resp = self.client.post(
            "/admin/oidc/providers",
            json={
                "name": "Keycloak",
                "client_id": "gm-app",
                "client_secret": "supersecret",
                "issuer": "https://keycloak.example.com/realms/corp",
            },
            cookies=self.cookies,
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn("id", body)
        self.assertEqual(body["name"], "Keycloak")

    def test_create_then_list(self):
        self.client.post(
            "/admin/oidc/providers",
            json={
                "name": "Okta",
                "client_id": "cid",
                "client_secret": "csec",
                "issuer": "https://company.okta.com/oauth2/default",
            },
            cookies=self.cookies,
        )
        resp = self.client.get("/admin/oidc/providers", cookies=self.cookies)
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["name"], "Okta")

    def test_delete_provider(self):
        create_resp = self.client.post(
            "/admin/oidc/providers",
            json={
                "name": "ToDelete",
                "client_id": "c",
                "client_secret": "s",
                "issuer": "https://idp.example.com",
            },
            cookies=self.cookies,
        )
        pid = create_resp.json()["id"]
        del_resp = self.client.delete(f"/admin/oidc/providers/{pid}", cookies=self.cookies)
        self.assertEqual(del_resp.status_code, 204)
        self.assertEqual(self.client.get("/admin/oidc/providers", cookies=self.cookies).json(), [])

    def test_delete_nonexistent_404(self):
        resp = self.client.delete("/admin/oidc/providers/no-such-id", cookies=self.cookies)
        self.assertEqual(resp.status_code, 404)

    def test_admin_list_requires_auth(self):
        fresh = TestClient(self.client.app, raise_server_exceptions=True)
        resp = fresh.get("/admin/oidc/providers")
        self.assertIn(resp.status_code, {401, 403})

    def test_public_providers_list_no_auth(self):
        fresh = TestClient(self.client.app, raise_server_exceptions=True)
        resp = fresh.get("/auth/oidc/providers")
        self.assertEqual(resp.status_code, 200)

    def test_public_providers_hides_secret(self):
        self.client.post(
            "/admin/oidc/providers",
            json={
                "name": "Secret IdP",
                "client_id": "cid",
                "client_secret": "TOPSECRET",
                "issuer": "https://idp.example.com",
            },
            cookies=self.cookies,
        )
        fresh = TestClient(self.client.app, raise_server_exceptions=True)
        resp = fresh.get("/auth/oidc/providers")
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertNotIn("client_secret", body[0])
        self.assertIn("id", body[0])
        self.assertIn("name", body[0])

    def test_idp_presets_endpoint(self):
        fresh = TestClient(self.client.app, raise_server_exceptions=True)
        resp = fresh.get("/auth/oidc/idp-presets")
        self.assertEqual(resp.status_code, 200)
        ids = {p["id"] for p in resp.json()}
        self.assertIn("keycloak", ids)
        self.assertIn("entra", ids)


class TestOIDCFlow(unittest.TestCase):
    """Tests for the start/callback flow using mocked IdP responses."""

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
        self.client, self.admin_cookies = _make_admin_client(self.db_path)
        # Create a provider via the admin API
        resp = self.client.post(
            "/admin/oidc/providers",
            json={
                "name": "Test IdP",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "issuer": "https://idp.example.com",
            },
            cookies=self.admin_cookies,
        )
        self.provider_id = resp.json()["id"]

    def tearDown(self):
        self.tmp.cleanup()

    @patch("groupware_migrator.api.routers.oidc_router.build_authorization_url")
    def test_start_redirects_to_idp(self, mock_build):
        mock_build.return_value = "https://idp.example.com/authorize?code=123"
        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/start",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("idp.example.com", resp.headers["location"])

    @patch("groupware_migrator.api.routers.oidc_router.build_authorization_url")
    def test_start_sets_nonce_cookie(self, mock_build):
        mock_build.return_value = "https://idp.example.com/authorize"
        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/start",
            follow_redirects=False,
        )
        self.assertIn("_oidc_nonce", resp.cookies)

    def test_start_unknown_provider_404(self):
        resp = self.client.get("/auth/oidc/no-such-id/start", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)

    @patch("groupware_migrator.api.routers.oidc_router.exchange_code")
    @patch("groupware_migrator.api.routers.oidc_router.validate_id_token")
    @patch("groupware_migrator.api.routers.oidc_router.verify_state")
    def test_callback_provisions_new_user(self, mock_verify, mock_validate, mock_exchange):
        nonce = "testnonce123"
        mock_verify.return_value = nonce
        mock_exchange.return_value = {"id_token": "fake.id.token"}
        mock_validate.return_value = {
            "sub": "ext-user-001",
            "email": "sso-user@example.com",
            "nonce": nonce,
            "iss": "https://idp.example.com",
        }

        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/callback",
            params={"code": "auth-code-xyz", "state": f"{nonce}.fakesig"},
            cookies={"_oidc_nonce": nonce},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        # User should now exist in the store
        user = self.store.get_user_by_email("sso-user@example.com")
        self.assertIsNotNone(user)
        self.assertEqual(user["auth_backend"], "local")

    @patch("groupware_migrator.api.routers.oidc_router.exchange_code")
    @patch("groupware_migrator.api.routers.oidc_router.validate_id_token")
    @patch("groupware_migrator.api.routers.oidc_router.verify_state")
    def test_callback_sets_session_cookie(self, mock_verify, mock_validate, mock_exchange):
        nonce = "testnonce456"
        mock_verify.return_value = nonce
        mock_exchange.return_value = {"id_token": "fake.id.token"}
        mock_validate.return_value = {
            "sub": "ext-001",
            "email": "sso2@example.com",
            "nonce": nonce,
            "iss": "https://idp.example.com",
        }

        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/callback",
            params={"code": "code-abc", "state": f"{nonce}.sig"},
            cookies={"_oidc_nonce": nonce},
            follow_redirects=False,
        )
        self.assertIn("gm_session", resp.cookies)

    def test_callback_missing_code_400(self):
        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/callback",
            params={"state": "some.state"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)

    def test_callback_idp_error_param_400(self):
        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)

    @patch("groupware_migrator.api.routers.oidc_router.verify_state")
    def test_callback_bad_state_400(self, mock_verify):
        mock_verify.side_effect = ValueError("state signature invalid")
        resp = self.client.get(
            f"/auth/oidc/{self.provider_id}/callback",
            params={"code": "c", "state": "tampered.state"},
            cookies={"_oidc_nonce": "testnonce"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
