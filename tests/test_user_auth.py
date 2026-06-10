import tempfile
import unittest
from pathlib import Path

from groupware_migrator.engine.state import SQLiteStateStore, hash_password, verify_password


class TestPasswordHelpers(unittest.TestCase):
    def test_hash_is_not_plaintext(self):
        h = hash_password("secret123")
        self.assertNotEqual(h, "secret123")
        self.assertTrue(h.startswith("$2"))

    def test_verify_correct_password(self):
        h = hash_password("secret123")
        self.assertTrue(verify_password("secret123", h))

    def test_verify_wrong_password(self):
        h = hash_password("secret123")
        self.assertFalse(verify_password("wrong", h))


class TestUserStore(unittest.TestCase):
    def _store(self, tmp: str) -> SQLiteStateStore:
        return SQLiteStateStore(Path(tmp) / "state.db")

    def test_create_and_get_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            user_id = store.create_user(
                email="admin@example.com",
                password_hash=hash_password("pass"),
                is_admin=True,
            )
            self.assertIsNotNone(user_id)
            user = store.get_user_by_email("admin@example.com")
            self.assertIsNotNone(user)
            self.assertEqual(user["email"], "admin@example.com")
            self.assertTrue(bool(user["is_admin"]))

    def test_get_user_by_email_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertIsNone(store.get_user_by_email("nobody@example.com"))

    def test_duplicate_email_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.create_user(email="a@b.com", password_hash="x", is_admin=False)
            with self.assertRaises(Exception):
                store.create_user(email="a@b.com", password_hash="y", is_admin=False)

    def test_count_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertEqual(store.count_users(), 0)
            store.create_user(email="a@b.com", password_hash="x", is_admin=True)
            self.assertEqual(store.count_users(), 1)

    def test_list_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.create_user(email="a@b.com", password_hash="x", is_admin=True)
            store.create_user(email="b@b.com", password_hash="y", is_admin=False)
            users = store.list_users()
            self.assertEqual(len(users), 2)
            emails = {u["email"] for u in users}
            self.assertIn("a@b.com", emails)

    def test_get_user_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            uid = store.create_user(email="a@b.com", password_hash="x", is_admin=False)
            user = store.get_user_by_id(uid)
            self.assertIsNotNone(user)
            self.assertEqual(user["id"], uid)


class TestJWT(unittest.TestCase):
    SECRET = "test-secret-key"

    def test_create_and_decode_token(self):
        from groupware_migrator.api.auth import create_access_token, decode_access_token
        token = create_access_token(
            {"sub": "user-id", "email": "a@b.com", "is_admin": False},
            secret=self.SECRET,
        )
        self.assertIsInstance(token, str)
        payload = decode_access_token(token, secret=self.SECRET)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "user-id")
        self.assertEqual(payload["email"], "a@b.com")

    def test_decode_invalid_token_returns_none(self):
        from groupware_migrator.api.auth import decode_access_token
        self.assertIsNone(decode_access_token("not.a.token", secret=self.SECRET))

    def test_decode_wrong_secret_returns_none(self):
        from groupware_migrator.api.auth import create_access_token, decode_access_token
        token = create_access_token({"sub": "x"}, secret=self.SECRET)
        self.assertIsNone(decode_access_token(token, secret="wrong-secret"))


class TestApiKeyStore(unittest.TestCase):
    def _store(self, tmp: str) -> SQLiteStateStore:
        return SQLiteStateStore(Path(tmp) / "state.db")

    def _make_user(self, store: SQLiteStateStore) -> str:
        return store.create_user(
            email="u@example.com",
            password_hash=hash_password("pw"),
            is_admin=False,
        )

    def test_create_and_validate_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            user_id = self._make_user(store)
            key_id, raw_key = store.create_api_key(user_id=user_id, label="test")
            self.assertIsNotNone(key_id)
            self.assertIsNotNone(raw_key)
            user = store.validate_api_key(raw_key)
            self.assertIsNotNone(user)
            self.assertEqual(user["sub"], user_id)

    def test_validate_wrong_key_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertIsNone(store.validate_api_key("bad-key"))

    def test_list_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            user_id = self._make_user(store)
            store.create_api_key(user_id=user_id, label="key1")
            store.create_api_key(user_id=user_id, label="key2")
            keys = store.list_api_keys(user_id=user_id)
            self.assertEqual(len(keys), 2)
            for k in keys:
                self.assertNotIn("key_hash", k)

    def test_revoke_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            user_id = self._make_user(store)
            key_id, raw_key = store.create_api_key(user_id=user_id, label="x")
            deleted = store.revoke_api_key(key_id=key_id, user_id=user_id)
            self.assertTrue(deleted)
            self.assertIsNone(store.validate_api_key(raw_key))

    def test_revoke_wrong_user_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            uid1 = self._make_user(store)
            uid2 = store.create_user(email="b@b.com", password_hash="x", is_admin=False)
            key_id, _ = store.create_api_key(user_id=uid1, label="x")
            deleted = store.revoke_api_key(key_id=key_id, user_id=uid2)
            self.assertFalse(deleted)


class TestUserScopedJobs(unittest.TestCase):
    def _store(self, tmp: str) -> SQLiteStateStore:
        return SQLiteStateStore(Path(tmp) / "state.db")

    def _request(self):
        from groupware_migrator.models import MigrationRequest
        return MigrationRequest.from_dict({
            "source": {"protocol": "imap", "connection": {"host": "s", "username": "u", "password": "p"}},
            "destination": {"protocol": "imap", "connection": {"host": "d", "username": "u", "password": "p"}},
        })

    def test_create_job_stores_user_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            from groupware_migrator.models import MigrationPlan
            store = self._store(tmp)
            uid = store.create_user(email="a@b.com", password_hash="x", is_admin=False)
            job_id = store.create_job(self._request(), MigrationPlan(), user_id=uid)
            job = store.get_job(job_id)
            self.assertEqual(job["user_id"], uid)

    def test_list_jobs_filters_by_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            from groupware_migrator.models import MigrationPlan
            store = self._store(tmp)
            uid1 = store.create_user(email="a@b.com", password_hash="x", is_admin=False)
            uid2 = store.create_user(email="b@b.com", password_hash="x", is_admin=False)
            store.create_job(self._request(), MigrationPlan(), user_id=uid1)
            store.create_job(self._request(), MigrationPlan(), user_id=uid2)
            jobs1 = store.list_jobs(user_id=uid1)
            self.assertEqual(len(jobs1), 1)
            jobs2 = store.list_jobs(user_id=uid2)
            self.assertEqual(len(jobs2), 1)
            all_jobs = store.list_jobs()
            self.assertEqual(len(all_jobs), 2)
