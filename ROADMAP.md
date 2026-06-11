# Groupware Migrator — Roadmap

> **All 7 phases complete** as of June 2026. 159 tests · all green.

An interactive HTML version with full feature details is available at [`roadmap.html`](roadmap.html).

---

## What's Shipped

| Area | Features |
|---|---|
| Protocols | IMAP / POP3 / CalDAV / CardDAV connectors |
| Jobs | Background jobs, crash recovery, incremental sync, cursor persistence |
| Batch | CSV batch migration with per-row overrides, batch preflight |
| Reliability | Preflight checks, idempotency fingerprinting, retry policy, graceful drain |
| Streaming | SSE live progress streams for jobs and batches |
| Auth | JWT + HttpOnly cookies, API keys, per-user job scoping |
| 2FA | TOTP (Google Authenticator) with recovery codes |
| RBAC | Four roles: `viewer`, `operator`, `admin`, `super_admin` |
| Security | Rate limiting, security headers, login brute-force protection |
| Scheduling | Cron-style and interval-based recurring jobs (`0 2 * * *`, `6h`) |
| Webhooks | HMAC-SHA256-signed POST notifications on `job.completed/failed/cancelled` |
| Organizations | Multi-tenant workspaces with owner/admin/member roles |
| Vault | Fernet encryption for scheduled job credentials (`VAULT_KEY`) |
| Admin | User management, system stats, admin audit log, data retention cleanup |
| Observability | Structured audit events, JSON/CSV report export |
| Ops | Docker + docker-compose, nginx guide, SQLite backup/restore CLI |
| API | REST API versioned at both `/api/*` and `/api/v1/*` |
| UI | Dark-glass dashboard, login page, admin panel, schedules page, org page |

---

## Phase 1 — Backend Foundation ✅

*~2 weeks · Clean the engine room before adding features*

- **Split `app.py` into FastAPI routers** — 640-line monolith → `routers/jobs.py`, `routers/batches.py`, `routers/providers.py`
- **Pydantic request models** — Typed `JobRequest`, `BatchRequest`, `BatchPreflightRequest` on all POST endpoints
- **Crash recovery** — On startup, stuck `running` jobs are marked `failed` with a descriptive error
- **Structured logging** — `logging` module throughout engine and connectors; `LOG_LEVEL` env var
- **Remove duplicate helpers** — `_batch_status_from_counts` was copy-pasted in two files; consolidated
- **FastAPI lifespan** — Replaced deprecated `@app.on_event` hooks with `@asynccontextmanager` lifespan

---

## Phase 2 — Auth & Multi-tenancy ✅

*~2–3 weeks · Safe to deploy for multiple users*

- **User model & admin bootstrap** — `users` table with bcrypt passwords; first admin from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars
- **JWT session management** — `HttpOnly; SameSite=Strict` cookies; `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- **Per-user job & batch scoping** — `user_id` on `jobs` and `batches`; non-admins see only their own
- **Login UI** — Dark-glass `/login` page; 401 API responses redirect to `/login`
- **API keys** — Per-user keys stored SHA-256-hashed; Bearer token auth alongside cookies; revocable from UI

---

## Phase 3 — Frontend Quality ✅

*~1–2 weeks · A product-grade UI that doesn't lose your work*

- **Split `app.js` into ES modules** — 1231-line monolith → `js/api.js`, `js/form.js`, `js/streams.js`, `js/jobs.js`, `js/batches.js`, `js/main.js`
- **Form state persistence** — Host/port/workload/protocol/TLS/sync-mode saved to `localStorage`; passwords excluded
- **Loading states on action buttons** — Disabled + spinner during API calls; prevents double-submission
- **Mobile responsive layout** — CSS breakpoints at 900px and 600px; stacked layout on small screens

---

## Phase 4 — Production Hardening ✅

*~3–4 weeks · Deployable in real environments*

**Deployment**
- Docker multi-stage image + `docker-compose.yml` with volume-mounted DB and env file
- Nginx reverse proxy guide with HTTPS termination and correct SSE headers
- `groupware-migrator backup` / `restore` CLI commands with integrity verification

**Security**
- Login rate limiting (sliding-window, keyed by IP)
- `POST /auth/change-password` (requires current password)
- Security headers middleware: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `CSP`

**Reliability**
- `/health/live` (always 200) and `/health/ready` (checks DB) — Kubernetes-compatible
- Job retry policy with configurable `max_retries` and exponential backoff
- Graceful shutdown: drains running jobs before exit on SIGTERM

---

## Phase 5 — Scheduling & Automation ✅

*~4–5 weeks · Migrations on autopilot*

**Scheduled Jobs**
- Cron expressions (`0 2 * * *`) and interval strings (`6h`, `30m`) stored in `scheduled_jobs` table
- `SchedulerThread` daemon fires incremental migrations when due (30s tick)
- Pause / resume schedules without deleting them; next-run shown in dashboard

**Webhooks**
- Register callback URLs via `POST /api/webhooks`
- Fires on `job.completed`, `job.failed`, `job.cancelled` with HMAC-SHA256 signature
- 3-attempt retry with (0, 5, 15)s delays on 5xx responses
- Delivery history at `GET /api/webhooks/{id}/deliveries`

**Job Queue**
- `priority` field (high/normal/low) per job
- `POST /api/jobs/{job_id}/cancel` — graceful cancellation preserving checkpoints

---

## Phase 6 — Admin Dashboard & Observability ✅

*~3–4 weeks · Visibility for administrators*

**Admin UI** (`/admin`)
- User management: create, deactivate, reset passwords, view per-user job counts
- System overview: total jobs, success rate, items migrated, active jobs — auto-refreshing

**Audit & Compliance**
- `admin_audit_events` table records all admin actions; exportable as CSV
- Data retention cleanup: delete jobs/batches/audit events older than N days

---

## Phase 7 — Enterprise Auth & Multi-tenancy ✅

*~6–8 weeks · Enterprise authentication and tenant isolation*

**Authentication**
- TOTP 2FA (Google Authenticator compatible): `GET /auth/totp/setup`, `POST /auth/totp/confirm`, `POST /auth/totp/disable`
- Login step-up: returns `{"totp_required": true}` when 2FA is enabled and no code provided
- 10 SHA-256-hashed recovery codes generated at TOTP enrollment

**Multi-tenancy**
- `organizations` + `org_memberships` tables; creator auto-assigned `owner`
- Roles within orgs: `owner`, `admin`, `member`
- `RBAC`: `viewer` → `operator` → `admin` → `super_admin`; enforced via `fastapi.Depends`

**Vault & API**
- Credential vault: Fernet encryption for scheduled job credentials; `VAULT_KEY` env var (32-byte base64)
- REST versioning: all endpoints available at `/api/*` and `/api/v1/*`

---

## Remaining Gaps — Deferred, Not Forgotten

These were planned but require external services or a separate integration phase:

| Item | Blocker |
|---|---|
| **SAML 2.0 / OIDC SSO** | Infrastructure groundwork exists; waiting on IdP selection (Okta, Auth0, Keycloak) |
| **LDAP / Active Directory bind** | Needs a live LDAP server and org-specific schema decisions |
| **Email notifications (SMTP)** | Webhooks cover the same use case; add once SMTP is configured |
| **Plugin / connector SDK** | Connector base classes are ready; packaging and docs story missing |
| **Prometheus metrics endpoint** | Straightforward to add when production monitoring is needed |

---

## Intentionally Out of Scope

- PostgreSQL / MySQL backend
- Tasks & Notes workloads
- Exchange EWS protocol
- Real-time push (WebSockets beyond SSE)
- Mobile apps
- Billing / subscription tiers
- Horizontal scaling (multi-instance)
- Cloud storage for state (S3, GCS)
- On-the-fly protocol conversion (IMAP→CalDAV)
- Full-text search across migrated content

---

## Guiding Principles

**Local-first by default** — SQLite stays the default. New features must work without external services.

**Backward compatibility** — Existing job payloads, CLI configs, and CSV formats must keep working. Schema migrations are always additive.

**Tests gate every phase** — Each phase ships with tests. Integration tests hit real SQLite, not mocks.

**Security is not an afterthought** — Rate limiting, audit logs, and RBAC shipped in Phases 4–7, not "later".

**Simple beats clever** — No message queues, no microservices, no ORM until the current model demonstrably breaks.

**Deploy anywhere** — A single Docker container with one volume mount is the happy path.

---

## Architecture Decisions

| Decision | Choice |
|---|---|
| **Sequence** | Foundation first — auth on top of well-structured code is easier to maintain |
| **Auth model** | Multi-user email + password (not SSO — avoids IdP dependency for early product) |
| **Session storage** | HttpOnly cookies, not localStorage — prevents XSS token theft |
| **Frontend** | Vanilla JS ES modules — no bundler required |
| **Database** | SQLite — appropriate for self-hosted SaaS at this scale |
| **Concurrency** | ThreadPoolExecutor — no external queue until threading model breaks |
