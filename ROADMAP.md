# Groupware Migrator — Roadmap

> **Phases 1–12 complete** as of June 2026. 348 tests · all green.

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
| Email | SMTP-based HTML email notifications per-user opt-in on job completion, failure, cancellation |
| LDAP / AD | Active Directory / LDAP bind — coexisting auth backend with auto-provisioning |
| Plugin SDK | Connector plugin system — third-party packages register new protocols via entry points |
| Providers (DE) | German / DACH provider presets: GMX, WEB.DE, T-Online, Posteo, mailbox.org, IONOS, Strato, Freenet |
| Tasks / Notes | VTODO and VJOURNAL workload types over CalDAV; full UI, validation, and runner support |
| Observability | Prometheus metrics at `GET /metrics` (admin-only); enriched `/health/ready` with `db_latency_ms` |
| SSO / OIDC | OIDC authorization-code flow; admin CRUD for providers; IdP presets for Keycloak, Okta, Auth0, Entra ID, Google |
| MS Graph | Microsoft Graph API source connector for Exchange Online mail migration (OAuth2 + paged MIME download) |
| Providers (Enterprise) | Nextcloud and Exchange Online provider presets with MS Graph and IMAP OAuth2 defaults |
| PostgreSQL backend | Opt-in via `DATABASE_URL`; `psycopg2`-backed drop-in for SQLiteStateStore; `pg_dump` for backups |
| Redis job queue | `RedisJobManager` drop-in for BackgroundJobManager; `groupware-migrator-worker` CLI; horizontal scaling |
| Data export | `GET /admin/export` JSON export; `GET /admin/backup/download` SQLite file download (WAL-checkpointed) |

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

---

## Phase 8 — Workload Completion & Provider Coverage ✅

*Completed June 2026*

- **Tasks workload (VTODO)** ✅ — CalDAV source/destination; UI workload selector; 12 tests
- **Notes workload (VJOURNAL)** ✅ — same runner path; VJOURNAL content type
- **German / DACH provider presets** ✅ — GMX, WEB.DE, T-Online, Posteo, mailbox.org, IONOS, Strato, Freenet

---

## Phase 9 — Enterprise Protocol Connectors ✅

*Completed June 2026*

- **Microsoft Graph API connector** ✅ — OAuth2 bearer auth; paginated folder listing; raw MIME download; `SourceProtocol.MSGRAPH`
- **Nextcloud provider preset** ✅ — CalDAV/CardDAV with `/remote.php/dav` paths and app-password auth notes
- **Exchange Online provider preset** ✅ — MS Graph + IMAP OAuth2 defaults with Entra ID token URL templates
- **Exchange EWS connector** — deferred; modern workloads use MS Graph instead
- **Plugin SDK** ✅ — shipped in Phase 8; third-party connectors register via Python entry points

---

## Phase 10 — Observability & Metrics ✅

*Completed June 2026*

- **Prometheus `GET /metrics`** ✅ — admin-only; build_info, jobs by status, items migrated/skipped/failed, users, schedules, batches
- **Health check enrichment** ✅ — `/health/ready` returns `db_latency_ms` and `active_jobs`
- **Extended `system_stats()`** ✅ — items_skipped_total, items_failed_total, jobs_cancelled, scheduled_jobs_total

---

## Phase 11 — SSO & Enterprise Authentication ✅

*Completed June 2026*

- **OIDC / OAuth2 authorization-code flow** ✅ — CSRF-protected start/callback; nonce HMAC signed with JWT_SECRET
- **User provisioning** ✅ — first-login creates account; admin claim promotes to admin role
- **IdP presets** ✅ — Keycloak, Okta, Auth0, Microsoft Entra ID (Azure AD), Google Workspace
- **Admin CRUD** ✅ — `POST/GET/DELETE /admin/oidc/providers`; client_secret never returned in public listing
- **SAML 2.0** — deferred; OIDC covers the majority of enterprise SSO use cases

---

## Phase 12 — Scale & Resilience ✅

*Complete — June 2026*

- **PostgreSQL backend** — opt-in via `DATABASE_URL`; `psycopg2`-backed `PostgresStateStore` inherits from SQLiteStateStore; SQL translated at call sites (`?`→`%s`, `INSERT OR IGNORE`→`ON CONFLICT DO NOTHING`); SQLite remains the default
- **Horizontal scaling** — `RedisJobManager` drop-in for `BackgroundJobManager`; jobs pushed to a Redis LIST, cancellation via Redis key, `groupware-migrator-worker` CLI worker process; install with `pip install "groupware-migrator[redis]"`
- **Data export** — `GET /admin/backup/download` (WAL-checkpointed SQLite download; 501 for PostgreSQL) and `GET /admin/export` (full JSON state dump)

---

## Intentionally Out of Scope

- Real-time push (WebSockets beyond SSE)
- Mobile apps
- Billing / subscription tiers
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
