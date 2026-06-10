# Groupware Migrator — Product Roadmap Design

**Date:** 2026-06-10  
**Direction:** Product / SaaS  
**Sequence:** Foundation → Auth → Frontend  
**Auth model:** Multi-user email + password, per-user job scoping

---

## Context

The codebase is functionally complete for MVP workloads (mail, calendar, contacts) but has structural issues that would become liabilities as a product. This spec covers three sequential improvement phases plus a backlog of future work.

---

## Phase 1 — Backend Foundation (~2 weeks)

Clean up structural problems before adding auth on top of them.

### 1.1 Split `app.py` into routers

`app.py` is 640 lines with all routes defined inside a single `create_app` factory. Split into:
- `api/routers/jobs.py` — plan, run, start, resume, list, get, events, report, stream endpoints
- `api/routers/batches.py` — preview, preflight, start, list, get, stream endpoints
- `api/routers/providers.py` — provider presets endpoint

Each router mounts under `/api` with a prefix. `create_app` becomes a thin wiring function.

### 1.2 Pydantic request models

All POST endpoints currently accept raw `dict` payloads. Replace with typed Pydantic models:
- `JobRequest` — wraps the existing `MigrationRequest.from_dict` logic
- `BatchRequest` — csv_content, base_request, batch_name, allow_partial
- `BatchPreflightRequest` — adds limit

FastAPI auto-validates, auto-documents, and surfaces errors before they reach engine code.

### 1.3 Crash recovery

On startup (inside the lifespan context), query all jobs with `status = 'running'` and set them to `status = 'failed'` with `last_error = 'Server restarted while job was running.'`. Currently these jobs hang in the running state forever after a crash or restart.

### 1.4 Structured logging

Add Python `logging` calls throughout `engine/runner.py`, `engine/preflight.py`, and all connectors. Log level configurable via `LOG_LEVEL` env var (default `INFO`). Replace any bare `print` statements.

### 1.5 Remove duplicated code

`_batch_status_from_counts` / `_derive_batch_status` is implemented identically in both `app.py` and `state.py`. Remove the app-layer copy; call `state.py`'s version.

### 1.6 Fix deprecated lifecycle hook

`@app.on_event("startup")` / `@app.on_event("shutdown")` are deprecated in FastAPI ≥ 0.93. Replace with a single `@asynccontextmanager` lifespan function passed to `FastAPI(lifespan=...)`. The lifespan function handles crash recovery (1.3) and background worker shutdown.

---

## Phase 2 — Auth & Multi-tenancy (~2–3 weeks)

Make the product safe to deploy for multiple users.

### 2.1 User model

New `users` table in SQLite:

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
```

Password hashing: `bcrypt` via `passlib`. First admin user bootstrapped from `ADMIN_EMAIL` + `ADMIN_PASSWORD` env vars on first startup (only if no users exist).

### 2.2 Session management

JWT tokens stored in `HttpOnly; SameSite=Strict` cookies. Token payload: `{ sub: user_id, email, is_admin, exp }`. Secret key from `JWT_SECRET` env var (required in production). Token lifetime: 8 hours, configurable via `JWT_TTL_HOURS`.

New endpoints:
- `POST /auth/login` — validates credentials, sets cookie
- `POST /auth/logout` — clears cookie
- `GET /auth/me` — returns current user info

Auth middleware (`fastapi.Depends`) applied to all `/api/*` routes. Public routes: `/`, `/login`, `/health`, `/auth/login`.

### 2.3 Per-user job scoping

Add `user_id TEXT` column (nullable) to `jobs` and `batches` tables via SQLite `ALTER TABLE`. Existing rows get `user_id = NULL`, which is treated as "admin-owned" — visible only to admin users. All new jobs/batches set `user_id` from the authenticated session. List/get/stream endpoints filter by authenticated `user_id`; admin users see all rows including NULL-owner legacy records.

### 2.4 Login UI

Minimal `/login` HTML page (new `login.html` static file). On auth success, redirect to `/`. On 401 from any API call, redirect to `/login`. Matches existing dark-glass visual style.

### 2.5 API key support

Optional per-user API keys for CLI/script access:

```sql
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    key_hash TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
```

Bearer token auth (`Authorization: Bearer <key>`) accepted alongside cookie sessions. Admin UI section for key generation and revocation.

---

## Phase 3 — Frontend Quality (~1–2 weeks)

A product-grade UI that feels polished and doesn't lose your work.

### 3.1 ES modules

Split `app.js` (1231 lines, single global scope) into native ES modules:
- `js/api.js` — all fetch wrappers, unified error handling
- `js/form.js` — form read/write, provider preset application, workload–protocol alignment
- `js/streams.js` — EventSource lifecycle management
- `js/jobs.js` — job list rendering, job detail rendering
- `js/batches.js` — batch list/detail rendering, CSV preview
- `js/main.js` — entry point, wires everything together

`index.html` loads `js/main.js` with `type="module"`. No bundler required.

### 3.2 Form state persistence

Save to `localStorage` on every `input`/`change` event: source/destination host, port, username, workload, protocol, provider selection, TLS profile, SSL toggle, sync mode. Restore on `DOMContentLoaded`. Passwords and OAuth tokens intentionally excluded.

Key prefix: `gm_form_` to avoid collisions.

### 3.3 Loading states

Disable all action buttons (`Run Preflight`, `Build Plan`, `Start Background Job`, `Start Batch`, etc.) and replace button text with a spinner during pending API calls. Re-enable and restore text on response (success or error). Prevents double-submission.

### 3.4 Mobile responsiveness

Add CSS breakpoints at 900px and 600px. Below 900px: form panel and dashboard panel stack vertically. Below 600px: two-column grid fields collapse to single column. Test on common tablet sizes.

### 3.5 Copy fixes

Update hero subtitle from `"Asynchronous IMAP/POP3 migration to IMAP..."` to accurately describe CalDAV/CardDAV support. Update `<title>` and any other stale references.

---

## Backlog

Not blocking the product. Prioritize after Phase 3.

| Item | Notes |
|------|-------|
| Docker image + `docker-compose.yml` | Prerequisite for easy self-hosted deployment |
| GitHub Actions CI | Lint (`ruff`) + tests (`unittest`) on push/PR |
| OpenAPI schema export | FastAPI auto-generates; add export script and version pinning |
| Adaptive SSE polling | Back off interval when jobs are idle; currently fixed 1–1.5s |
| Tasks / Notes workload | Already modeled in domain layer, just needs connector + runner wiring |
| WebDAV library | Replace raw HTTP in `dav.py` with `caldav` library for robustness |
| Webhooks on job completion | POST callback URL with job report payload |
| Rate limiting on batch preflight | Prevent abuse on the preflight endpoint |
| Role-based access | Admin / operator / viewer roles beyond `is_admin` flag |
| Password reset flow | Email-based reset; needs SMTP config |

---

## Decisions recorded

- **Sequence:** Foundation first — auth built on top of well-structured code is easier to maintain
- **Auth model:** Multi-user email+password (not SSO — avoids IdP dependency for early product)
- **Session storage:** HttpOnly cookies (not localStorage) — prevents XSS token theft
- **No bundler:** ES modules are sufficient given the project's vanilla JS philosophy
- **SQLite stays:** No database migration needed at this scale; SQLite is appropriate for SaaS with moderate concurrency
