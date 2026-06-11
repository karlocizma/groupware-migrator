# Phase 2 — Auth & Multi-tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-user email+password authentication with JWT HttpOnly cookie sessions, per-user job scoping, and API key support to the groupware-migrator FastAPI application.

**Architecture:** Users are stored in SQLite (same `state.db` via `SQLiteStateStore`). Auth is handled by a new `api/auth.py` module providing FastAPI `Depends` functions. A new `api/routers/auth_router.py` exposes `/auth/*` endpoints. All existing `/api/*` routes are protected by `Depends(require_user)` injected at include time in `app.py`. The first admin is bootstrapped from env vars on first startup.

**Tech Stack:** Python 3.11+, FastAPI ≥ 0.115, Pydantic v2, `passlib[bcrypt]` for password hashing, `PyJWT>=2.8` for JWT tokens, SQLite via existing `sqlite3` stdlib.

---

## File Map

**New files:**
- `src/groupware_migrator/api/auth.py` — JWT creation/validation + FastAPI `Depends` functions
- `src/groupware_migrator/api/routers/auth_router.py` — `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/keys/*`
- `src/groupware_migrator/api/static/login.html` — login page (dark-glass style)
- `tests/test_user_auth.py` — tests for user/API-key store methods

**Modified files:**
- `pyproject.toml` — add `passlib[bcrypt]>=1.7`, `PyJWT>=2.8`
- `src/groupware_migrator/engine/state.py` — users + api_keys tables; new methods
- `src/groupware_migrator/engine/background.py` — `start_job` accepts `user_id`
- `src/groupware_migrator/api/app.py` — admin bootstrap, auth router, route protection
- `src/groupware_migrator/api/routers/jobs.py` — inject `current_user`, pass `user_id`
- `src/groupware_migrator/api/routers/batches.py` — same

---

## Task 1: Add auth dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

  In `pyproject.toml`, change the `dependencies` list from:
  ```toml
  dependencies = [
    "fastapi>=0.115,<1.0",
    "uvicorn>=0.30,<1.0"
  ]
  ```
  To:
  ```toml
  dependencies = [
    "fastapi>=0.115,<1.0",
    "uvicorn>=0.30,<1.0",
    "passlib[bcrypt]>=1.7",
    "PyJWT>=2.8"
  ]
  ```

- [ ] **Step 2: Install**

  ```bash
  pip install -e ".[dev]"
  ```

  Expected: installs passlib, bcrypt, PyJWT. No errors.

- [ ] **Step 3: Verify imports work**

  ```bash
  PYTHONPATH=src python3 -c "import passlib.context; import jwt; print('OK')"
  ```

  Expected: `OK`

- [ ] **Step 4: Run full test suite — all passing**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

  Expected: `Ran 56 tests ... OK`

- [ ] **Step 5: Commit**

  ```bash
  git add pyproject.toml
  git commit -m "chore: add passlib[bcrypt] and PyJWT auth dependencies"
  ```

---

## Task 2: User model in state.py (TDD)

**Files:**
- Modify: `src/groupware_migrator/engine/state.py`
- Create: `tests/test_user_auth.py`

- [ ] **Step 1: Write failing tests in `tests/test_user_auth.py`**

  ```python
  import tempfile
  import unittest
  from pathlib import Path

  from groupware_migrator.engine.state import SQLiteStateStore, hash_password, verify_password


  class TestPasswordHelpers(unittest.TestCase):
      def test_hash_is_not_plaintext(self):
          h = hash_password("secret123")
          self.assertNotEqual(h, "secret123")
          self.assertTrue(h.startswith("$2"))  # bcrypt prefix

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
  ```

- [ ] **Step 2: Run — expect FAIL**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestPasswordHelpers tests.test_user_auth.TestUserStore -v 2>&1 | tail -5
  ```

  Expected: `ImportError` or `AttributeError` (functions/methods not yet defined).

- [ ] **Step 3: Add `users` table to schema and implement methods in `state.py`**

  At the top of `state.py`, add the import:
  ```python
  from passlib.context import CryptContext

  _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


  def hash_password(password: str) -> str:
      return _pwd_context.hash(password)


  def verify_password(password: str, hashed: str) -> bool:
      return _pwd_context.verify(password, hashed)
  ```

  In `_initialize_schema`, add to the `executescript` (append before the closing `"""`):
  ```sql
  CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      is_admin INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
  );
  ```

  Add these methods to `SQLiteStateStore` after `recover_stuck_jobs`:
  ```python
  def create_user(self, *, email: str, password_hash: str, is_admin: bool = False) -> str:
      user_id = str(uuid.uuid4())
      now = _utcnow_iso()
      with self._lock, self._connection() as connection:
          connection.execute(
              """
              INSERT INTO users (id, email, password_hash, is_admin, created_at)
              VALUES (?, ?, ?, ?, ?)
              """,
              (user_id, email.lower().strip(), password_hash, 1 if is_admin else 0, now),
          )
      return user_id

  def get_user_by_email(self, email: str) -> dict[str, Any] | None:
      with self._lock, self._connection() as connection:
          cursor = connection.execute(
              "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
          )
          row = cursor.fetchone()
      return dict(row) if row else None

  def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
      with self._lock, self._connection() as connection:
          cursor = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,))
          row = cursor.fetchone()
      return dict(row) if row else None

  def count_users(self) -> int:
      with self._lock, self._connection() as connection:
          cursor = connection.execute("SELECT COUNT(*) FROM users")
          row = cursor.fetchone()
      return int(row[0]) if row else 0

  def list_users(self) -> list[dict[str, Any]]:
      with self._lock, self._connection() as connection:
          cursor = connection.execute(
              "SELECT id, email, is_admin, created_at FROM users ORDER BY created_at ASC"
          )
          rows = cursor.fetchall()
      return [dict(row) for row in rows]
  ```

- [ ] **Step 4: Run user tests — expect PASS**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestPasswordHelpers tests.test_user_auth.TestUserStore -v 2>&1 | tail -5
  ```

  Expected: `Ran 7 tests ... OK`

- [ ] **Step 5: Run full suite — all passing**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

  Expected: `Ran 63 tests ... OK` (7 new + 56 existing)

- [ ] **Step 6: Commit**

  ```bash
  git add src/groupware_migrator/engine/state.py tests/test_user_auth.py
  git commit -m "feat: add user model with bcrypt password hashing to state store"
  ```

---

## Task 3: JWT + cookie auth module

**Files:**
- Create: `src/groupware_migrator/api/auth.py`
- Modify: `tests/test_user_auth.py` (add JWT tests)

- [ ] **Step 1: Add JWT tests to `tests/test_user_auth.py`**

  Append this class to the file:
  ```python
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
  ```

- [ ] **Step 2: Run JWT tests — expect FAIL**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestJWT -v 2>&1 | tail -5
  ```

  Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Create `src/groupware_migrator/api/auth.py`**

  ```python
  from __future__ import annotations

  import os
  from datetime import datetime, timedelta, timezone

  import jwt
  from fastapi import Depends, HTTPException, Request
  from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

  from groupware_migrator.engine.state import SQLiteStateStore

  COOKIE_NAME = "gm_session"
  JWT_ALGORITHM = "HS256"
  _bearer = HTTPBearer(auto_error=False)


  def _jwt_secret(request: Request) -> str:
      return str(request.app.state.jwt_secret)


  def create_access_token(payload: dict, *, secret: str, ttl_hours: int = 8) -> str:
      data = {**payload, "exp": datetime.now(timezone.utc) + timedelta(hours=ttl_hours)}
      return jwt.encode(data, secret, algorithm=JWT_ALGORITHM)


  def decode_access_token(token: str, *, secret: str) -> dict | None:
      try:
          return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
      except jwt.PyJWTError:
          return None


  def get_current_user(
      request: Request,
      credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
  ) -> dict | None:
      secret = _jwt_secret(request)
      state_store: SQLiteStateStore = request.app.state.state_store

      # Cookie path (browser sessions)
      token = request.cookies.get(COOKIE_NAME)
      if token:
          payload = decode_access_token(token, secret=secret)
          if payload:
              return payload

      # Bearer path (API keys)
      if credentials:
          user = state_store.validate_api_key(credentials.credentials)
          if user:
              return user

      return None


  def require_user(current_user: dict | None = Depends(get_current_user)) -> dict:
      if not current_user:
          raise HTTPException(status_code=401, detail="Authentication required.")
      return current_user


  def require_admin(current_user: dict = Depends(require_user)) -> dict:
      if not current_user.get("is_admin"):
          raise HTTPException(status_code=403, detail="Admin access required.")
      return current_user
  ```

- [ ] **Step 4: Run JWT tests — expect PASS**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestJWT -v 2>&1 | tail -5
  ```

  Expected: `Ran 3 tests ... OK`

  Note: `validate_api_key` doesn't exist in state.py yet — the test doesn't call `get_current_user` so this is fine for now.

- [ ] **Step 5: Run full suite**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

  Expected: `Ran 66 tests ... OK`

- [ ] **Step 6: Commit**

  ```bash
  git add src/groupware_migrator/api/auth.py tests/test_user_auth.py
  git commit -m "feat: add JWT token creation/validation and auth dependency functions"
  ```

---

## Task 4: Auth router

**Files:**
- Create: `src/groupware_migrator/api/routers/auth_router.py`

- [ ] **Step 1: Create `src/groupware_migrator/api/routers/auth_router.py`**

  ```python
  from __future__ import annotations

  import os

  from fastapi import APIRouter, Depends, HTTPException, Request, Response
  from pydantic import BaseModel

  from groupware_migrator.api.auth import (
      COOKIE_NAME,
      create_access_token,
      require_admin,
      require_user,
  )
  from groupware_migrator.engine.state import SQLiteStateStore, hash_password, verify_password


  class LoginPayload(BaseModel):
      email: str
      password: str


  class CreateUserPayload(BaseModel):
      email: str
      password: str
      is_admin: bool = False


  class CreateApiKeyPayload(BaseModel):
      label: str = ""


  def _ttl_hours() -> int:
      return int(os.environ.get("JWT_TTL_HOURS", "8"))


  def create_auth_router(state_store: SQLiteStateStore) -> APIRouter:
      router = APIRouter()

      @router.post("/auth/login")
      def login(payload: LoginPayload, request: Request, response: Response) -> dict:
          user = state_store.get_user_by_email(payload.email)
          if not user or not verify_password(payload.password, str(user["password_hash"])):
              raise HTTPException(status_code=401, detail="Invalid email or password.")
          token = create_access_token(
              {"sub": str(user["id"]), "email": str(user["email"]), "is_admin": bool(user["is_admin"])},
              secret=str(request.app.state.jwt_secret),
              ttl_hours=_ttl_hours(),
          )
          response.set_cookie(
              COOKIE_NAME,
              token,
              httponly=True,
              samesite="strict",
              secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
              max_age=_ttl_hours() * 3600,
          )
          return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"])}

      @router.post("/auth/logout")
      def logout(response: Response) -> dict:
          response.delete_cookie(COOKIE_NAME)
          return {"ok": True}

      @router.get("/auth/me")
      def me(current_user: dict = Depends(require_user)) -> dict:
          return {
              "id": current_user.get("sub"),
              "email": current_user.get("email"),
              "is_admin": bool(current_user.get("is_admin")),
          }

      # Admin: create users
      @router.post("/auth/users")
      def create_user(
          payload: CreateUserPayload,
          _admin: dict = Depends(require_admin),
      ) -> dict:
          try:
              user_id = state_store.create_user(
                  email=payload.email,
                  password_hash=hash_password(payload.password),
                  is_admin=payload.is_admin,
              )
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc
          return {"id": user_id, "email": payload.email, "is_admin": payload.is_admin}

      # Admin: list users
      @router.get("/auth/users")
      def list_users(_admin: dict = Depends(require_admin)) -> dict:
          return {"items": state_store.list_users()}

      # API keys: create
      @router.post("/auth/keys")
      def create_api_key(
          payload: CreateApiKeyPayload,
          current_user: dict = Depends(require_user),
      ) -> dict:
          user_id = str(current_user["sub"])
          key_id, raw_key = state_store.create_api_key(user_id=user_id, label=payload.label)
          return {"key_id": key_id, "key": raw_key, "label": payload.label}

      # API keys: list
      @router.get("/auth/keys")
      def list_api_keys(current_user: dict = Depends(require_user)) -> dict:
          user_id = str(current_user["sub"])
          return {"items": state_store.list_api_keys(user_id=user_id)}

      # API keys: revoke
      @router.delete("/auth/keys/{key_id}")
      def revoke_api_key(
          key_id: str,
          current_user: dict = Depends(require_user),
      ) -> dict:
          user_id = str(current_user["sub"])
          deleted = state_store.revoke_api_key(key_id=key_id, user_id=user_id)
          if not deleted:
              raise HTTPException(status_code=404, detail="API key not found.")
          return {"ok": True}

      return router
  ```

- [ ] **Step 2: Verify the router file imports without error**

  ```bash
  PYTHONPATH=src python3 -c "
  # state_store methods (validate_api_key etc.) don't exist yet — import only checks syntax
  try:
      from groupware_migrator.api.routers.auth_router import create_auth_router
      print('Import OK')
  except ImportError as e:
      print('ImportError:', e)
  "
  ```

  Expected: `Import OK` (the missing state methods will raise at runtime, not import time).

- [ ] **Step 3: Run full suite — still 66 tests passing**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add src/groupware_migrator/api/routers/auth_router.py
  git commit -m "feat: add auth router (login, logout, me, users, API keys)"
  ```

---

## Task 5: API key support in state.py

**Files:**
- Modify: `src/groupware_migrator/engine/state.py`
- Modify: `tests/test_user_auth.py`

- [ ] **Step 1: Add API key tests to `tests/test_user_auth.py`**

  Append this class:
  ```python
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
              # Validate returns user dict
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
              # Raw key NOT in list response
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
  ```

- [ ] **Step 2: Run API key tests — expect FAIL**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestApiKeyStore -v 2>&1 | tail -5
  ```

  Expected: `AttributeError` (methods not yet defined).

- [ ] **Step 3: Add `api_keys` table and methods to `state.py`**

  Add to `_initialize_schema` executescript:
  ```sql
  CREATE TABLE IF NOT EXISTS api_keys (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      key_hash TEXT NOT NULL UNIQUE,
      label TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      last_used_at TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_api_keys_user
      ON api_keys(user_id);
  ```

  Add these methods to `SQLiteStateStore` (after the user methods):
  ```python
  def create_api_key(self, *, user_id: str, label: str = "") -> tuple[str, str]:
      """Create an API key. Returns (key_id, raw_key). Store only the hash."""
      import secrets
      key_id = str(uuid.uuid4())
      raw_key = secrets.token_urlsafe(32)
      key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
      now = _utcnow_iso()
      with self._lock, self._connection() as connection:
          connection.execute(
              """
              INSERT INTO api_keys (id, user_id, key_hash, label, created_at)
              VALUES (?, ?, ?, ?, ?)
              """,
              (key_id, user_id, key_hash, label, now),
          )
      return key_id, raw_key

  def validate_api_key(self, raw_key: str) -> dict[str, Any] | None:
      """Validate a raw API key. Returns a user-like dict for auth (sub, email, is_admin)
      or None if invalid. Updates last_used_at."""
      key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
      with self._lock, self._connection() as connection:
          cursor = connection.execute(
              """
              SELECT k.id AS key_id, u.id AS user_id, u.email, u.is_admin
              FROM api_keys k
              JOIN users u ON k.user_id = u.id
              WHERE k.key_hash = ?
              """,
              (key_hash,),
          )
          row = cursor.fetchone()
          if not row:
              return None
          connection.execute(
              "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
              (_utcnow_iso(), str(row["key_id"])),
          )
      return {
          "sub": str(row["user_id"]),
          "email": str(row["email"]),
          "is_admin": bool(row["is_admin"]),
      }

  def list_api_keys(self, *, user_id: str) -> list[dict[str, Any]]:
      with self._lock, self._connection() as connection:
          cursor = connection.execute(
              """
              SELECT id, label, created_at, last_used_at
              FROM api_keys WHERE user_id = ?
              ORDER BY created_at DESC
              """,
              (user_id,),
          )
          rows = cursor.fetchall()
      return [dict(row) for row in rows]

  def revoke_api_key(self, *, key_id: str, user_id: str) -> bool:
      """Delete an API key. Returns True if deleted, False if not found or wrong user."""
      with self._lock, self._connection() as connection:
          cursor = connection.execute(
              "DELETE FROM api_keys WHERE id = ? AND user_id = ?",
              (key_id, user_id),
          )
          return cursor.rowcount > 0
  ```

- [ ] **Step 4: Run API key tests — expect PASS**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestApiKeyStore -v 2>&1 | tail -5
  ```

  Expected: `Ran 5 tests ... OK`

- [ ] **Step 5: Run full suite**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

  Expected: `Ran 71 tests ... OK`

- [ ] **Step 6: Commit**

  ```bash
  git add src/groupware_migrator/engine/state.py tests/test_user_auth.py
  git commit -m "feat: add API key store methods (create, validate, list, revoke)"
  ```

---

## Task 6: Wire auth into app.py (bootstrap + route protection)

**Files:**
- Modify: `src/groupware_migrator/api/app.py`

- [ ] **Step 1: Update `src/groupware_migrator/api/app.py`**

  Replace the entire file with:

  ```python
  from __future__ import annotations

  import logging
  import os
  from contextlib import asynccontextmanager
  from pathlib import Path

  from fastapi import Depends, FastAPI, HTTPException
  from fastapi.responses import FileResponse
  from fastapi.staticfiles import StaticFiles

  from groupware_migrator.api.auth import require_user
  from groupware_migrator.api.routers.auth_router import create_auth_router
  from groupware_migrator.api.routers.batches import create_batches_router
  from groupware_migrator.api.routers.jobs import create_jobs_router
  from groupware_migrator.api.routers.providers import create_providers_router
  from groupware_migrator.engine.background import BackgroundJobManager
  from groupware_migrator.engine.runner import MigrationRunner
  from groupware_migrator.engine.state import SQLiteStateStore, hash_password


  def _configure_logging() -> None:
      level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
      level = getattr(logging, level_name, logging.INFO)
      logging.basicConfig(
          level=level,
          format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
          datefmt="%Y-%m-%dT%H:%M:%S",
      )


  def _get_jwt_secret() -> str:
      secret = os.environ.get("JWT_SECRET", "")
      if not secret:
          import secrets
          secret = secrets.token_hex(32)
          logging.getLogger(__name__).warning(
              "JWT_SECRET not set; using a random secret. Sessions will not survive restarts."
          )
      return secret


  def _bootstrap_admin(state_store: SQLiteStateStore) -> None:
      if state_store.count_users() > 0:
          return
      email = os.environ.get("ADMIN_EMAIL", "")
      password = os.environ.get("ADMIN_PASSWORD", "")
      if not email or not password:
          logging.getLogger(__name__).warning(
              "No users exist and ADMIN_EMAIL/ADMIN_PASSWORD not set. "
              "Set these env vars to create the first admin account."
          )
          return
      state_store.create_user(
          email=email,
          password_hash=hash_password(password),
          is_admin=True,
      )
      logging.getLogger(__name__).info("Created first admin user: %s", email)


  def create_app(*, state_db_path: str = "data/state.db") -> FastAPI:
      _configure_logging()

      state_store = SQLiteStateStore(Path(state_db_path))
      runner = MigrationRunner(state_store=state_store)
      background_jobs = BackgroundJobManager(state_store=state_store, runner=runner)
      jwt_secret = _get_jwt_secret()

      @asynccontextmanager
      async def lifespan(app: FastAPI):
          try:
              recovered = state_store.recover_stuck_jobs()
              if recovered:
                  logging.getLogger(__name__).warning(
                      "Recovered %d job(s) stuck in running state on startup.", recovered
                  )
          except Exception as exc:
              logging.getLogger(__name__).error("Failed to recover stuck jobs on startup: %s", exc)
          try:
              _bootstrap_admin(state_store)
          except Exception as exc:
              logging.getLogger(__name__).error("Admin bootstrap failed: %s", exc)
          yield
          try:
              background_jobs.shutdown(wait=False)
          except Exception as exc:
              logging.getLogger(__name__).error("Error during background worker shutdown: %s", exc)

      app = FastAPI(title="Groupware Migrator", version="0.4.0", lifespan=lifespan)
      app.state.state_store = state_store
      app.state.runner = runner
      app.state.background_jobs = background_jobs
      app.state.jwt_secret = jwt_secret

      static_dir = Path(__file__).resolve().parent / "static"
      if static_dir.exists():
          app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

      @app.get("/")
      def ui_index() -> FileResponse:
          index_file = static_dir / "index.html"
          if not index_file.exists():
              raise HTTPException(status_code=404, detail="UI assets not found.")
          return FileResponse(index_file)

      @app.get("/login")
      def login_page() -> FileResponse:
          login_file = static_dir / "login.html"
          if not login_file.exists():
              raise HTTPException(status_code=404, detail="Login page not found.")
          return FileResponse(login_file)

      @app.get("/health")
      def health() -> dict:
          return {"status": "ok"}

      # Auth router — public (no auth required)
      auth_router = create_auth_router(state_store)
      app.include_router(auth_router)

      # Protected routers — require authenticated user
      auth_dep = [Depends(require_user)]
      jobs_router = create_jobs_router(state_store, background_jobs, runner)
      batches_router = create_batches_router(state_store, background_jobs)
      providers_router = create_providers_router()

      for prefix in ("/api", ""):
          app.include_router(jobs_router, prefix=prefix, dependencies=auth_dep)
          app.include_router(batches_router, prefix=prefix, dependencies=auth_dep)
          app.include_router(providers_router, prefix=prefix, dependencies=auth_dep)

      return app
  ```

- [ ] **Step 2: Run full test suite — expect all passing**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

  Expected: `Ran 71 tests ... OK`

- [ ] **Step 3: Smoke-test app starts with JWT secret**

  ```bash
  PYTHONPATH=src JWT_SECRET=test-secret ADMIN_EMAIL=admin@test.com ADMIN_PASSWORD=secret123 python3 -c "
  from groupware_migrator.api.app import create_app
  app = create_app()
  routes = [r.path for r in app.routes]
  assert any('/auth/login' in r for r in routes), 'Missing /auth/login'
  assert any('/api/jobs' in r for r in routes), 'Missing /api/jobs'
  print('Routes:', len(routes))
  print('OK')
  "
  ```

  Expected: prints routes count and `OK`.

- [ ] **Step 4: Commit**

  ```bash
  git add src/groupware_migrator/api/app.py
  git commit -m "feat: wire auth into app (JWT secret, admin bootstrap, route protection)"
  ```

---

## Task 7: Per-user job scoping

**Files:**
- Modify: `src/groupware_migrator/engine/state.py`
- Modify: `src/groupware_migrator/engine/background.py`
- Modify: `src/groupware_migrator/api/routers/jobs.py`
- Modify: `src/groupware_migrator/api/routers/batches.py`
- Modify: `tests/test_user_auth.py`

- [ ] **Step 1: Add user scoping tests to `tests/test_user_auth.py`**

  Append this class:
  ```python
  class TestUserScopedJobs(unittest.TestCase):
      def _store(self, tmp: str) -> SQLiteStateStore:
          return SQLiteStateStore(Path(tmp) / "state.db")

      def _request(self):
          from groupware_migrator.models import MigrationRequest, MigrationPlan
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
              # Admin sees all
              all_jobs = store.list_jobs()
              self.assertEqual(len(all_jobs), 2)
  ```

- [ ] **Step 2: Run tests — expect FAIL**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestUserScopedJobs -v 2>&1 | tail -5
  ```

  Expected: `TypeError` (create_job doesn't accept user_id yet).

- [ ] **Step 3: Add `user_id` column to `jobs` and `batches` via ALTER TABLE in `state.py`**

  In `_initialize_schema`, after the existing `executescript` block, add:
  ```python
  # Migrate: add user_id column if absent (idempotent)
  for table in ("jobs", "batches"):
      try:
          with self._lock, self._connection() as connection:
              connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
      except Exception:
          pass  # Column already exists
  ```

  Update `create_job` to accept and store `user_id`:
  ```python
  def create_job(self, request: MigrationRequest, plan: MigrationPlan, user_id: str | None = None) -> str:
      job_id = str(uuid.uuid4())
      now = _utcnow_iso()
      request_json = json.dumps(request.to_dict(redact_password=True), sort_keys=True)
      plan_json = json.dumps(plan.to_dict(), sort_keys=True)
      with self._lock, self._connection() as connection:
          connection.execute(
              """
              INSERT INTO jobs (
                  job_id, job_name, status, source_protocol, destination_protocol,
                  request_json, plan_json, dry_run, created_at, updated_at, user_id
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
              """,
              (
                  job_id, request.job_name, JobStatus.PENDING.value,
                  request.source.protocol.value, request.destination.protocol.value,
                  request_json, plan_json, 1 if request.options.dry_run else 0,
                  now, now, user_id,
              ),
          )
      return job_id
  ```

  Update `list_jobs` to accept optional `user_id` filter:
  ```python
  def list_jobs(self, *, limit: int = 20, user_id: str | None = None) -> list[dict[str, Any]]:
      safe_limit = max(min(int(limit), 500), 1)
      with self._lock, self._connection() as connection:
          if user_id is not None:
              cursor = connection.execute(
                  "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                  (user_id, safe_limit),
              )
          else:
              cursor = connection.execute(
                  "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                  (safe_limit,),
              )
          rows = cursor.fetchall()
      return [dict(row) for row in rows]
  ```

  Similarly update `create_batch` (find it in state.py and add `user_id=None` parameter, store in INSERT).
  Update `list_batches` to accept `user_id=None` filter (same pattern as `list_jobs`).

- [ ] **Step 4: Update `background.py` to thread user_id through**

  In `background.py`, update `start_job`:
  ```python
  def start_job(self, request: MigrationRequest, user_id: str | None = None) -> str:
      job_id = self._state_store.create_job(request=request, plan=MigrationPlan(), user_id=user_id)
      self._submit(job_id=job_id, request=request)
      return job_id
  ```

- [ ] **Step 5: Update `routers/jobs.py` — inject current_user, pass user_id**

  Add import at top of `src/groupware_migrator/api/routers/jobs.py`:
  ```python
  from groupware_migrator.api.auth import require_user
  ```

  Update `start_background_job` to extract and pass user_id:
  ```python
  @router.post("/jobs/start")
  def start_background_job(
      payload: JobPayload,
      current_user: dict = Depends(require_user),
  ) -> dict:
      try:
          request = _migration_request_from_payload(payload.model_dump())
          user_id = str(current_user.get("sub", ""))
          job_id = background_jobs.start_job(request=request, user_id=user_id)
          job_row = state_store.get_job(job_id)
          if not job_row:
              raise RuntimeError("Unable to read background job after creation.")
          return _job_response(job_row, running=True, include_payload=True)
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc
  ```

  Update `list_jobs` to filter by user (non-admins see only their jobs):
  ```python
  @router.get("/jobs")
  def list_jobs(
      limit: int = Query(default=20, ge=1, le=200),
      current_user: dict = Depends(require_user),
  ) -> dict:
      user_id = None if current_user.get("is_admin") else str(current_user.get("sub", ""))
      rows = state_store.list_jobs(limit=limit, user_id=user_id)
      return {
          "items": [
              _job_response(
                  row,
                  running=background_jobs.is_running(str(row["job_id"])),
                  include_payload=False,
              )
              for row in rows
          ]
      }
  ```

  Update `jobs_stream` similarly (add `current_user` dep, pass `user_id` to `list_jobs`).

- [ ] **Step 6: Update `routers/batches.py` — inject current_user, pass user_id to start_batch**

  Add import:
  ```python
  from groupware_migrator.api.auth import require_user
  ```

  Update `start_batch` to pass `user_id` to `state_store.create_batch`:
  ```python
  @router.post("/batches/start")
  def start_batch(
      payload: BatchPayload,
      current_user: dict = Depends(require_user),
  ) -> dict:
      user_id = str(current_user.get("sub", ""))
      # ... existing logic, change create_batch call to:
      batch_id = state_store.create_batch(
          batch_name=payload.batch_name,
          total_rows=preview["total_rows"],
          user_id=user_id,
      )
      # rest unchanged
  ```

  Update `list_batches` to filter by user (same pattern as list_jobs).

- [ ] **Step 7: Run user scoping tests — expect PASS**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_user_auth.TestUserScopedJobs -v 2>&1 | tail -5
  ```

  Expected: `Ran 2 tests ... OK`

- [ ] **Step 8: Run full test suite**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

  Expected: all tests pass.

- [ ] **Step 9: Commit**

  ```bash
  git add src/groupware_migrator/engine/state.py \
          src/groupware_migrator/engine/background.py \
          src/groupware_migrator/api/routers/jobs.py \
          src/groupware_migrator/api/routers/batches.py \
          tests/test_user_auth.py
  git commit -m "feat: add per-user job and batch scoping with user_id filtering"
  ```

---

## Task 8: Login UI

**Files:**
- Create: `src/groupware_migrator/api/static/login.html`

- [ ] **Step 1: Create `src/groupware_migrator/api/static/login.html`**

  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sign In — Groupware Migrator</title>
    <style>
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
      :root {
        --bg: #0f1117; --surface: #1a1d27; --border: rgba(255,255,255,0.08);
        --accent: #6c8fff; --text: #e2e8f0; --muted: #8892a4;
        --red: #f87171; --radius: 12px;
      }
      body {
        background: var(--bg); color: var(--text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        min-height: 100vh; display: flex; align-items: center; justify-content: center;
      }
      .orb { position: fixed; border-radius: 50%; filter: blur(120px); pointer-events: none; opacity: 0.22; }
      .orb-a { width: 500px; height: 500px; background: #3b4ff0; top: -150px; left: -100px; }
      .orb-b { width: 400px; height: 400px; background: #7c3aed; bottom: -80px; right: -80px; }
      .card {
        position: relative; z-index: 1;
        background: var(--surface); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 40px 36px; width: 100%; max-width: 400px;
      }
      h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
      .subtitle { color: var(--muted); font-size: 0.88rem; margin-bottom: 28px; }
      label { display: block; font-size: 0.82rem; color: var(--muted); margin-bottom: 6px; }
      input[type=email], input[type=password] {
        width: 100%; background: rgba(255,255,255,0.05); border: 1px solid var(--border);
        border-radius: 8px; padding: 10px 14px; color: var(--text); font-size: 0.95rem;
        outline: none; transition: border-color 0.15s;
      }
      input:focus { border-color: var(--accent); }
      .field { margin-bottom: 18px; }
      button[type=submit] {
        width: 100%; margin-top: 8px; padding: 11px;
        background: var(--accent); border: none; border-radius: 8px;
        color: #fff; font-size: 0.95rem; font-weight: 600; cursor: pointer;
        transition: opacity 0.15s;
      }
      button[type=submit]:hover { opacity: 0.88; }
      button[type=submit]:disabled { opacity: 0.5; cursor: not-allowed; }
      .error {
        background: rgba(248,113,113,0.12); border: 1px solid rgba(248,113,113,0.3);
        border-radius: 8px; padding: 10px 14px; color: var(--red);
        font-size: 0.87rem; margin-top: 16px; display: none;
      }
      .error.visible { display: block; }
    </style>
  </head>
  <body>
    <div class="orb orb-a"></div>
    <div class="orb orb-b"></div>
    <div class="card">
      <h1>Sign In</h1>
      <p class="subtitle">Groupware Migrator</p>
      <form id="login-form">
        <div class="field">
          <label for="email">Email</label>
          <input id="email" type="email" autocomplete="email" required placeholder="admin@example.com" />
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input id="password" type="password" autocomplete="current-password" required placeholder="••••••••" />
        </div>
        <button type="submit" id="submit-btn">Sign In</button>
        <div class="error" id="error-msg"></div>
      </form>
    </div>
    <script>
      document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('submit-btn');
        const errEl = document.getElementById('error-msg');
        btn.disabled = true;
        btn.textContent = 'Signing in…';
        errEl.classList.remove('visible');
        try {
          const res = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              email: document.getElementById('email').value,
              password: document.getElementById('password').value,
            }),
          });
          if (res.ok) {
            window.location.href = '/';
          } else {
            const data = await res.json().catch(() => ({}));
            errEl.textContent = data.detail || 'Invalid email or password.';
            errEl.classList.add('visible');
          }
        } catch (err) {
          errEl.textContent = 'Network error. Please try again.';
          errEl.classList.add('visible');
        } finally {
          btn.disabled = false;
          btn.textContent = 'Sign In';
        }
      });
    </script>
  </body>
  </html>
  ```

- [ ] **Step 2: Verify login page is served**

  ```bash
  PYTHONPATH=src JWT_SECRET=s ADMIN_EMAIL=a@b.com ADMIN_PASSWORD=pw python3 -c "
  from groupware_migrator.api.app import create_app
  app = create_app()
  routes = [r.path for r in app.routes]
  assert '/login' in routes, 'Missing /login route'
  print('OK — /login route registered')
  "
  ```

  Expected: `OK — /login route registered`

- [ ] **Step 3: Run full test suite — all passing**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add src/groupware_migrator/api/static/login.html
  git commit -m "feat: add login UI with dark-glass design"
  ```

---

## Self-Review Checklist

- [x] **2.1 User model:** `users` table, bcrypt via passlib, `create_user`, `get_user_by_email`, `count_users`, `list_users`, `get_user_by_id` — Task 2
- [x] **Admin bootstrap from env vars:** `ADMIN_EMAIL` + `ADMIN_PASSWORD`, runs in lifespan — Task 6
- [x] **2.2 Session management:** JWT in `gm_session` HttpOnly cookie, `JWT_SECRET` env var, `/auth/login`, `/auth/logout`, `/auth/me` — Tasks 3, 4, 6
- [x] **2.3 Per-user job scoping:** `user_id` nullable column, `list_jobs`/`list_batches` filter, `create_job`/`create_batch` stores user_id — Task 7
- [x] **2.4 Login UI:** `login.html` with dark-glass style, POST /auth/login, redirect on success — Task 8
- [x] **2.5 API key support:** `api_keys` table, `create_api_key`, `validate_api_key`, `list_api_keys`, `revoke_api_key`, Bearer token path in `get_current_user`, `/auth/keys` CRUD — Tasks 5, 3, 4
- [x] **Route protection:** All `/api/*` routes protected via `Depends(require_user)` at include time — Task 6
- [x] **Public routes:** `/`, `/login`, `/health`, `/auth/login` are unprotected
- [x] **Backward compat:** `user_id` column added via `ALTER TABLE` (nullable, no DEFAULT needed) — existing rows get NULL (admin-visible)
- [x] **`create_batch` signature:** Task 7 Step 6 references updating `create_batch` in `state.py` to accept `user_id=None` — ensure this is implemented alongside `list_batches` update

**Note on `create_batch`:** Find `create_batch` in `state.py`, add `user_id: str | None = None` parameter, include it in the INSERT statement. The INSERT currently uses fixed columns — add `user_id` the same way `create_job` does in Task 7 Step 3.
