# LDAP / Active Directory Authentication Design

**Date:** 2026-06-11  
**Status:** Approved

## Goal

Allow enterprise users to log in with their Active Directory credentials instead of local passwords. Local accounts (including the bootstrap admin) continue to work unchanged. LDAP is a coexisting backend, not a replacement.

---

## Architecture

Three new pieces, one change to an existing one:

1. **`engine/ldap_auth.py`** (new) — `LDAPAuthBackend`; handles connect, bind, search, re-bind
2. **`engine/state.py`** (modified) — `auth_backend` column on `users` table; guard in `change_password`
3. **`api/routers/auth_router.py`** (modified) — updated login flow; guards on `change-password` and TOTP setup; new admin status endpoint
4. **`api/app.py`** (modified) — instantiate `LDAPAuthBackend`, pass to `create_auth_router`

One new dependency: `ldap3` (pure-Python, no system libs).

---

## LDAP Configuration (env vars)

| Variable | Default | Notes |
|---|---|---|
| `LDAP_HOST` | — | Required to enable. Feature silently disabled when absent. |
| `LDAP_PORT` | `389` (or `636` if `LDAP_USE_SSL=true`) | |
| `LDAP_USE_SSL` | `false` | Full LDAPS — wraps connection in TLS from the start |
| `LDAP_USE_STARTTLS` | `false` | Upgrade plain connection with STARTTLS after connect |
| `LDAP_BIND_DN` | — | Service account DN for searching, e.g. `CN=svc,OU=SvcAccts,DC=corp,DC=example` |
| `LDAP_BIND_PASSWORD` | — | Service account password |
| `LDAP_BASE_DN` | — | Search base, e.g. `OU=Users,DC=corp,DC=example` |
| `LDAP_USER_FILTER` | `(userPrincipalName={email})` | `{email}` substituted at lookup time |
| `LDAP_EMAIL_ATTR` | `mail` | Attribute to read canonical email from |
| `LDAP_DEFAULT_ROLE` | `operator` | Role assigned to auto-provisioned LDAP users |

`LDAP_HOST` absent → `LDAPAuthBackend.is_configured()` returns `False` → login falls through to local-only behavior.

---

## Database

### Modified table: `users`

One new column added via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in the schema migration block:

```sql
ALTER TABLE users ADD COLUMN auth_backend TEXT NOT NULL DEFAULT 'local'
```

All existing users get `'local'`. LDAP-provisioned users get `'ldap'`. Values are mutually exclusive — a user cannot switch backends.

### Auto-provisioned LDAP user row

```python
state_store.create_user(
    email=ldap_info["email"],
    password_hash="!",          # unusable sentinel — bcrypt never called for ldap users
    role=ldap_default_role,
    auth_backend="ldap",
)
```

`create_user` gains an `auth_backend: str = "local"` parameter.

The login flow for `auth_backend='ldap'` users never calls `verify_password` — it goes directly to `LDAPAuthBackend.authenticate()`. The `!` sentinel is a safety net only: it ensures no local password auth can ever succeed for LDAP accounts even if a code path bypasses the `auth_backend` check.

---

## `engine/ldap_auth.py`

```python
class LDAPAuthBackend:
    def is_configured(self) -> bool:
        """True if LDAP_HOST env var is set."""

    def authenticate(self, email: str, password: str) -> dict | None:
        """
        Returns {"email": str, "display_name": str} on success.
        Returns None on wrong password or user not found (caller cannot distinguish — avoids enumeration).
        Raises LDAPAuthError on connectivity/configuration problems.
        """
```

### `authenticate` internals

1. Build `ldap3.Server` from `LDAP_HOST`, `LDAP_PORT`, `LDAP_USE_SSL`
2. Open `ldap3.Connection` with `LDAP_BIND_DN` / `LDAP_BIND_PASSWORD` (anonymous if both absent)
3. Apply STARTTLS if `LDAP_USE_STARTTLS=true`
4. Search `LDAP_BASE_DN` with filter `LDAP_USER_FILTER.format(email=email)`, scope `SUBTREE`, attributes `[LDAP_EMAIL_ATTR, "displayName", "cn"]`
5. If no entry → return `None`
6. Re-bind as found entry DN with the user-supplied `password`
7. If re-bind fails → return `None`
8. Return `{"email": entry[LDAP_EMAIL_ATTR] or email, "display_name": entry.get("displayName") or entry.get("cn") or email}`

### Error handling

- `ldap3.core.exceptions.LDAPException` and connection errors → raise `LDAPAuthError(str(exc))`
- Wrong password / user not found → `return None` (no exception)
- `LDAPAuthError` is caught in the login endpoint and converted to HTTP 503

---

## Login Flow (`POST /auth/login`)

```
1. Rate-limit check (unchanged)
2. Look up user by email in local DB

3a. User found, auth_backend='local'
    → bcrypt verify_password()
    → TOTP check if enabled
    → issue JWT (unchanged)

3b. User found, auth_backend='ldap'
    → LDAPAuthBackend.authenticate(email, password)
    → None → 401 "Invalid email or password."
    → success → issue JWT (no TOTP check)

3c. User not found, LDAP configured
    → LDAPAuthBackend.authenticate(email, password)
    → None → 401
    → success → auto-provision user (create_user with auth_backend='ldap')
    → issue JWT

3d. User not found, LDAP not configured
    → 401

4. LDAPAuthError raised at any point → 503 "LDAP server unreachable."

5. Rate-limit clear on success (unchanged)
```

---

## Auth Router Changes

### Guards on existing endpoints

**`POST /auth/change-password`** — add at the top of the handler:
```python
if current_user_row.get("auth_backend") == "ldap":
    raise HTTPException(400, "Password change is not available for LDAP accounts.")
```

**`GET /auth/totp/setup`** — add at the top:
```python
if current_user_row.get("auth_backend") == "ldap":
    raise HTTPException(400, "TOTP is not available for LDAP accounts.")
```

(TOTP login check in `login` already skips TOTP because LDAP users will always have `totp_enabled=0`.)

### New endpoint

**`GET /api/admin/ldap/status`** (admin only):

```json
{"configured": true, "host": "ldap.corp.example"}
```

or when unconfigured:

```json
{"configured": false, "host": null}
```

No credentials or bind DN exposed.

---

## `app.py` changes

```python
from groupware_migrator.engine.ldap_auth import LDAPAuthBackend

ldap_backend = LDAPAuthBackend()
auth_router = create_auth_router(state_store, ldap_backend=ldap_backend)
```

`create_auth_router` gains `ldap_backend: LDAPAuthBackend | None = None`.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| LDAP not configured | Silent no-op; local auth only |
| LDAP user, wrong password | `None` returned; 401 to client |
| LDAP user not in directory | `None` returned; 401 to client |
| LDAP server unreachable | `LDAPAuthError` raised; 503 to client |
| LDAP user tries change-password | 400 |
| LDAP user tries TOTP setup | 400 |

---

## Testing

All `ldap3` calls mocked via `unittest.mock.patch`. No real LDAP server required.

### `TestLDAPAuthBackend`
- `test_not_configured_when_no_host` — `is_configured()` returns `False`
- `test_configured_when_host_set` — `is_configured()` returns `True`
- `test_authenticate_success` — mock bind + search returns user; method returns dict
- `test_authenticate_wrong_password` — re-bind raises `LDAPBindError`; method returns `None`
- `test_authenticate_user_not_found` — search returns empty; method returns `None`
- `test_authenticate_raises_on_connectivity_error` — connection raises; method raises `LDAPAuthError`

### `TestLDAPLoginFlow`
- `test_local_user_unaffected` — user with `auth_backend='local'`, LDAP configured; bcrypt path used
- `test_existing_ldap_user_success` — user with `auth_backend='ldap'`; authenticate called
- `test_existing_ldap_user_wrong_password` — returns 401
- `test_auto_provision_on_first_ldap_login` — no local user; LDAP success; user created with role=`LDAP_DEFAULT_ROLE`
- `test_auto_provision_default_role` — auto-provisioned user gets `operator` by default
- `test_ldap_server_down_returns_503` — `LDAPAuthError` raised; login returns 503
- `test_no_user_no_ldap_returns_401` — LDAP not configured; 401

### `TestLDAPGuards`
- `test_change_password_blocked_for_ldap_user` — 400
- `test_totp_setup_blocked_for_ldap_user` — 400
- `test_change_password_allowed_for_local_user` — unchanged

### `TestLDAPStatusEndpoint`
- `test_returns_configured_true_when_host_set`
- `test_returns_configured_false_when_no_host`
- `test_requires_admin`
