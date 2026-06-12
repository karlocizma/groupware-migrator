# Groupware Migrator

Local-first tool for migrating email, calendar, contacts, tasks, and notes between servers. Runs as a web application or CLI. Supports IMAP, POP3, CalDAV, CardDAV, and Microsoft Graph with resumable background jobs, CSV batch migration, and a live-streaming dashboard.

## Features

- **Workloads:** `mail` (IMAP/POP3/MS Graph → IMAP), `calendar` (CalDAV → CalDAV), `contacts` (CardDAV → CardDAV), `tasks` (VTODO over CalDAV), `notes` (VJOURNAL over CalDAV)
- **Background jobs:** asynchronous execution with live SSE progress updates
- **Batch migration:** CSV-driven multi-user waves with per-row overrides
- **Incremental sync:** cursor-based delta sync; anchored to a completed base job or persisted cursors
- **Preflight checks:** validates source/destination connectivity and plan readiness before execution
- **Idempotency:** fingerprint-based duplicate prevention across runs
- **Auth modes:** password and OAuth/XOAUTH2 (direct access token or refresh-token exchange)
- **Provider presets:** Gmail, Microsoft 365, Yahoo, Zoho, Nextcloud, Exchange Online; German/DACH providers: GMX, WEB.DE, T-Online, Posteo, mailbox.org, IONOS, Strato, Freenet
- **MS Graph connector:** Exchange Online mail migration via Microsoft Graph API (OAuth2)
- **Multi-user:** JWT session authentication, per-user job scoping, API key support
- **Reports:** per-job structured audit events, JSON/CSV export
- **Scheduling:** cron-style and interval-based recurring jobs (e.g. `0 2 * * *`, `6h`)
- **Webhooks:** HMAC-SHA256-signed POST notifications on job completion/failure/cancellation
- **Email notifications:** SMTP-based HTML emails on job completion/failure/cancellation; per-user opt-in
- **2FA:** TOTP two-factor authentication with recovery codes (Google Authenticator / Authy)
- **RBAC:** four roles — `viewer`, `operator`, `admin`, `super_admin`
- **Organizations:** group users into workspaces with owner/admin/member roles
- **Credential vault:** optional AES encryption for scheduled job credentials (`VAULT_KEY`)
- **LDAP / AD:** Active Directory bind; auto-provisions accounts on first login; coexists with local auth
- **SSO / OIDC:** OIDC authorization-code flow; IdP presets for Keycloak, Okta, Auth0, Entra ID, Google
- **Observability:** Prometheus metrics at `GET /metrics` (admin-only); enriched `/health/ready`
- **Plugin SDK:** third-party connectors installed as Python packages via entry points
- **PostgreSQL:** opt-in via `DATABASE_URL`; SQLite remains the default
- **Redis job queue:** opt-in horizontal scaling via `groupware-migrator-worker` CLI

## Quick start

```bash
# 1. Create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 2. Install
pip install -e ".[dev]"

# 3. Start the web UI
ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=changeme ./start.sh

# 4. Open http://127.0.0.1:8000/login
```

## Docker

### docker-compose (recommended)

```bash
# 1. Create an env file
cp .env.example .env   # or create it manually — see Environment variables below

# 2. Build and start
docker compose up -d

# 3. Open http://localhost:8000/login
```

The container persists all state in `./data/state.db` (mounted as a volume). Logs are written to stdout and captured by Docker.

### Docker CLI

```bash
# Build
docker build -t groupware-migrator .

# Run
docker run -d \
  --name groupware-migrator \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -e ADMIN_EMAIL=admin@example.com \
  -e ADMIN_PASSWORD=changeme \
  -e JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  groupware-migrator
```

### docker-compose with PostgreSQL and Redis (horizontal scaling)

```yaml
# docker-compose.prod.yml
services:
  app:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    environment:
      DATABASE_URL: postgresql://gm:secret@db:5432/groupware
      REDIS_URL: redis://redis:6379
    depends_on: [db, redis]
    restart: unless-stopped

  worker:
    build: .
    command: groupware-migrator-worker --redis-url redis://redis:6379
    env_file: .env
    environment:
      DATABASE_URL: postgresql://gm:secret@db:5432/groupware
    depends_on: [db, redis]
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: gm
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: groupware
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    volumes: [redisdata:/data]

volumes:
  pgdata:
  redisdata:
```

### Environment file

Create a `.env` file for `docker-compose`:

```ini
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=changeme
JWT_SECRET=<output of: python3 -c "import secrets; print(secrets.token_hex(32))">
VAULT_KEY=<output of: python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode())">
COOKIE_SECURE=true
SITE_URL=https://migrate.example.com
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | random (sessions don't survive restart) | Secret key for JWT session tokens |
| `ADMIN_EMAIL` | — | Email for first admin account (created on first startup if no users exist) |
| `ADMIN_PASSWORD` | — | Password for first admin account |
| `JWT_TTL_HOURS` | `8` | Session token lifetime in hours |
| `COOKIE_SECURE` | `false` | Set to `true` in HTTPS production deployments |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `PORT` | `8000` | Port for the web server (used by `start.sh`) |
| `SHUTDOWN_DRAIN_TIMEOUT` | `30` | Seconds to wait for running jobs to finish before forced shutdown |
| `DATABASE_URL` | — | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`). Uses SQLite when absent. |
| `REDIS_URL` | `redis://localhost:6379` | Redis URL used by `groupware-migrator-worker`. Not needed for the default thread-based queue. |
| `VAULT_KEY` | — | 32-byte URL-safe base64 key for encrypting scheduled job credentials at rest. Generate: `python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode())"` |
| `SMTP_HOST` | — | Hostname of the SMTP server. Email notifications are disabled when absent. |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP auth username |
| `SMTP_PASSWORD` | — | SMTP auth password |
| `SMTP_FROM` | `SMTP_USER` | Sender address shown in email clients |
| `SMTP_TLS` | `starttls` | TLS mode: `starttls` \| `ssl` \| `none` |
| `SMTP_TIMEOUT` | `10` | Seconds before SMTP connection timeout |
| `SITE_URL` | — | Base URL used in email "View job" links (e.g. `https://migrate.example.com`) |
| `LDAP_HOST` | — | Hostname of the LDAP/AD server. LDAP auth is disabled when absent. |
| `LDAP_PORT` | `389` (`636` if SSL) | LDAP port |
| `LDAP_USE_SSL` | `false` | Use LDAPS (full TLS from connection start) |
| `LDAP_USE_STARTTLS` | `false` | Upgrade plain connection with STARTTLS |
| `LDAP_BIND_DN` | — | Service account DN for user search, e.g. `CN=svc,OU=SvcAccts,DC=corp,DC=example` |
| `LDAP_BIND_PASSWORD` | — | Service account password |
| `LDAP_BASE_DN` | — | Search base, e.g. `OU=Users,DC=corp,DC=example` |
| `LDAP_USER_FILTER` | `(userPrincipalName={email})` | LDAP search filter; `{email}` is substituted at login |
| `LDAP_EMAIL_ATTR` | `mail` | Attribute to read user's email from |
| `LDAP_DEFAULT_ROLE` | `operator` | Role assigned to auto-provisioned LDAP users |

## CLI usage

```bash
# Run a migration
groupware-migrator --config examples/migration-config.example.json --plan-only
groupware-migrator --config /path/to/migration.json --dry-run
groupware-migrator --config /path/to/migration.json --resume-job-id <job_id>
groupware-migrator --config /path/to/migration.json --state-db /path/to/state.db

# Backup the state database
groupware-migrator backup --output /path/to/backup.db
groupware-migrator backup --state-db /path/to/state.db --output /backup/state-$(date +%Y%m%d).db

# Restore from a backup
groupware-migrator restore --from /path/to/backup.db
groupware-migrator restore --from /path/to/backup.db --force  # overwrite existing db

# Redis worker (for horizontal scaling with RedisJobManager)
groupware-migrator-worker --redis-url redis://localhost:6379 --db-path data/state.db
# or with PostgreSQL:
DATABASE_URL=postgresql://user:pass@host/db groupware-migrator-worker --redis-url redis://localhost:6379
```

## Development

```bash
# Run all tests
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Run a single test file
PYTHONPATH=src python3 -m unittest tests/test_runner.py -v

# Lint
ruff check src tests

# Start manually (with reload)
uvicorn groupware_migrator.api.app:create_app --factory --reload
```

## API reference

All `/api/*` endpoints require authentication (JWT cookie or `Authorization: Bearer <api-key>`). All endpoints are also accessible at `/api/v1/*`.

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/login` | Log in; sets `gm_session` cookie. Pass `totp_code` if 2FA is enabled. |
| `POST` | `/auth/logout` | Clear session cookie |
| `GET` | `/auth/me` | Current user info (includes `role`) |
| `POST` | `/auth/users` | Create a user (admin only); supports `role` field |
| `GET` | `/auth/users` | List users (admin only) |
| `POST` | `/auth/keys` | Create an API key |
| `GET` | `/auth/keys` | List your API keys |
| `DELETE` | `/auth/keys/{key_id}` | Revoke an API key |
| `POST` | `/auth/change-password` | Change your password |
| `GET` | `/auth/totp/setup` | Generate TOTP secret + recovery codes |
| `POST` | `/auth/totp/confirm` | Confirm setup with a live TOTP code |
| `POST` | `/auth/totp/disable` | Disable 2FA (requires current password) |
| `GET` | `/auth/totp/status` | Check whether 2FA is enabled |
| `GET` | `/auth/notifications` | Get your email notification preferences |
| `PATCH` | `/auth/notifications` | Update your email notification preferences |
| `GET` | `/auth/oidc/providers` | List configured OIDC providers (public — for login page) |
| `GET` | `/auth/oidc/{provider_id}/start` | Begin OIDC login flow (redirects to IdP) |
| `GET` | `/auth/oidc/{provider_id}/callback` | OIDC callback (handles code exchange, issues session) |

### Jobs

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs/preflight` | Validate connectivity and plan readiness |
| `POST` | `/api/jobs/plan` | Build migration plan |
| `POST` | `/api/jobs/start` | Start a background job |
| `POST` | `/api/jobs/resume` | Resume an existing job |
| `POST` | `/api/jobs/run` | Run synchronously (blocking) |
| `GET` | `/api/jobs` | List recent jobs |
| `GET` | `/api/jobs/{job_id}` | Get full job details |
| `GET` | `/api/jobs/{job_id}/events` | List audit events |
| `GET` | `/api/jobs/{job_id}/report` | Export job report (`?format=json` or `csv`) |
| `GET` | `/api/jobs/stream` | SSE stream of recent jobs |
| `GET` | `/api/jobs/{job_id}/stream` | SSE stream for one job |

### Batches

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/batches/preview` | Validate CSV rows |
| `POST` | `/api/batches/preflight` | Row-level preflight for CSV batch |
| `POST` | `/api/batches/start` | Start a batch wave |
| `GET` | `/api/batches` | List recent batches |
| `GET` | `/api/batches/{batch_id}` | Get batch summary + rows |
| `GET` | `/api/batches/stream` | SSE stream of recent batches |
| `GET` | `/api/batches/{batch_id}/stream` | SSE stream for one batch |

### Providers

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/providers` | List provider presets with defaults and auth guidance |

### Schedules

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/schedules` | Create a recurring schedule (cron or interval) |
| `GET` | `/api/schedules` | List your schedules |
| `GET` | `/api/schedules/{id}` | Get a schedule |
| `PATCH` | `/api/schedules/{id}` | Update name, expression, or active state |
| `DELETE` | `/api/schedules/{id}` | Delete a schedule |

### Webhooks

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/webhooks` | Register a webhook endpoint |
| `GET` | `/api/webhooks` | List your webhooks |
| `GET` | `/api/webhooks/{id}` | Get a webhook |
| `DELETE` | `/api/webhooks/{id}` | Remove a webhook |
| `GET` | `/api/webhooks/{id}/deliveries` | View delivery history |

### Organizations

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/orgs` | Create an organization |
| `GET` | `/api/orgs` | List your organizations |
| `GET` | `/api/orgs/{id}` | Get an organization |
| `GET` | `/api/orgs/{id}/members` | List members |
| `POST` | `/api/orgs/{id}/members` | Add a member |
| `DELETE` | `/api/orgs/{id}/members/{user_id}` | Remove a member |
| `DELETE` | `/api/orgs/{id}` | Delete an org (admin only) |

### Admin

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/admin/stats` | System statistics |
| `GET` | `/api/admin/users` | List all users |
| `PATCH` | `/api/admin/users/{id}` | Update user role or active state |
| `POST` | `/api/admin/users/{id}/reset-password` | Reset a user's password |
| `GET` | `/api/admin/audit-log` | Admin audit log |
| `POST` | `/api/admin/cleanup` | Delete records older than N days |
| `POST` | `/api/admin/smtp/test` | Send a test email to verify SMTP config |
| `GET` | `/api/admin/ldap/status` | Check whether LDAP is configured |
| `GET` | `/api/admin/plugins` | List installed connector plugins |
| `GET` | `/api/admin/backup/download` | Download WAL-checkpointed SQLite file (SQLite only; 501 for PostgreSQL) |
| `GET` | `/api/admin/export` | Export full state as JSON (jobs, users, batches, audit events, OIDC providers) |
| `POST` | `/api/admin/oidc/providers` | Register an OIDC provider (admin only) |
| `GET` | `/api/admin/oidc/providers` | List OIDC providers (admin only; includes client_secret) |
| `DELETE` | `/api/admin/oidc/providers/{id}` | Remove an OIDC provider |
| `GET` | `/api/admin/oidc/idp-presets` | List built-in IdP presets (Keycloak, Okta, Auth0, Entra ID, Google) |

### Observability

| Method | Path | Description |
|---|---|---|
| `GET` | `/metrics` | Prometheus metrics (admin-only) |
| `GET` | `/health/live` | Liveness probe — always 200 |
| `GET` | `/health/ready` | Readiness probe — checks DB; returns `db_latency_ms` and `active_jobs` |

## CSV batch format

Each row represents one migration job. Columns in the CSV override the base request sent from the form.

**Required columns:** `source_username`, `source_password`, `destination_username`, `destination_password`

**Common override columns:** `job_name`, `workload`, `source_protocol`, `source_host`, `source_port`, `destination_host`, `destination_port`, `destination_root_collection`, `dry_run`, `max_errors`, `sync_mode`, `incremental_base_job_id`

**OAuth columns:** `source_auth_mode`, `source_oauth_access_token`, `source_oauth_refresh_token`, `source_oauth_client_id`, `source_oauth_client_secret`, `source_oauth_token_url`, `destination_auth_mode`, and equivalents.

See `examples/batch-users.example.csv` for a sample file.

## Architecture

```
groupware_migrator/
├── api/
│   ├── app.py              # FastAPI factory, lifespan, route mounting, scheduler start
│   ├── auth.py             # JWT, Depends: require_user/admin/operator/super_admin
│   ├── rate_limit.py       # In-memory sliding-window login rate limiter
│   ├── schemas.py          # Pydantic request models
│   └── routers/
│       ├── auth_router.py      # /auth/* (login, TOTP, API keys, user management)
│       ├── admin_router.py     # /admin/* (stats, users, audit log, cleanup, backup, export)
│       ├── jobs.py             # /jobs/* endpoints
│       ├── batches.py          # /batches/* endpoints
│       ├── scheduler_router.py # /schedules/* CRUD
│       ├── webhooks_router.py  # /webhooks/* CRUD + delivery log
│       ├── orgs_router.py      # /orgs/* CRUD + member management
│       ├── oidc_router.py      # /auth/oidc/* SSO flow + /admin/oidc/* CRUD
│       ├── metrics_router.py   # /metrics Prometheus endpoint
│       └── providers.py        # /providers endpoint
├── connectors/
│   ├── imap.py             # IMAP source + destination
│   ├── pop3.py             # POP3 source
│   ├── dav.py              # CalDAV + CardDAV (PROPFIND/GET/PUT/MKCOL)
│   ├── graph.py            # Microsoft Graph API source (Exchange Online mail)
│   ├── auth.py             # OAuth token resolution
│   └── factory.py          # Protocol → connector dispatch (+ plugin registry fallback)
├── engine/
│   ├── state.py            # SQLiteStateStore; all persistence
│   ├── postgres_state.py   # PostgresStateStore (opt-in via DATABASE_URL) + create_state_store()
│   ├── background.py       # BackgroundJobManager (ThreadPoolExecutor + webhooks + retry)
│   ├── redis_jobs.py       # RedisJobManager — Redis LIST-based queue (opt-in)
│   ├── rq_worker.py        # groupware-migrator-worker CLI entry point
│   ├── runner.py           # MigrationRunner; item iteration → upsert
│   ├── scheduler.py        # SchedulerThread; fires due cron/interval schedules
│   ├── cron.py             # Minimal 5-field cron expression parser
│   ├── webhooks.py         # WebhookDeliveryManager; HMAC-signed HTTP POST
│   ├── mailer.py           # MailDeliveryManager; SMTP email notifications
│   ├── ldap_auth.py        # LDAPAuthBackend; bind + auto-provision
│   ├── oidc.py             # OIDCProviderConfig, HMAC state helpers, IdP presets
│   ├── plugin_registry.py  # Entry-point-based connector plugin registry
│   ├── vault.py            # Fernet credential encryption (VAULT_KEY)
│   ├── planner.py          # MigrationPlan construction
│   ├── preflight.py        # Connectivity + plan validation
│   ├── idempotency.py      # Fingerprint-based duplicate detection
│   ├── batch.py            # CSV parsing + batch row building
│   └── reporting.py        # Audit event queries, JSON/CSV export
├── models/
│   └── domain.py           # MigrationRequest, MigrationPlan, enums
├── providers.py            # Provider preset catalog
└── cli.py                  # CLI entrypoint (+ backup/restore subcommands)
```

### State store

All persistence goes through `SQLiteStateStore` (or `PostgresStateStore` when `DATABASE_URL` is set). Key tables:

| Table | Purpose |
|---|---|
| `users` | Accounts with bcrypt hashes, `role`, `is_active`, TOTP columns, `auth_backend` |
| `api_keys` | SHA-256-hashed API keys per user |
| `jobs` | Migration jobs with status, counters, `user_id`, `priority`, `retry_count` |
| `batches` | Batch waves with summary counters, `user_id` |
| `batch_items` | Per-row job references within a batch |
| `checkpoints` | Per-job mailbox resume positions |
| `sync_cursors` | Incremental sync positions (keyed by identity hash) |
| `message_migrations` | Fingerprint ledger for idempotency |
| `audit_events` | Structured per-job event log |
| `admin_audit_events` | Admin action log (user changes, cleanup, etc.) |
| `scheduled_jobs` | Recurring migration schedules with cron/interval expressions |
| `webhooks` | Registered webhook endpoints with HMAC secrets |
| `webhook_deliveries` | Delivery attempt log per webhook |
| `organizations` | Multi-tenant workspaces |
| `org_memberships` | User–org membership with owner/admin/member roles |
| `notification_prefs` | Per-user email notification opt-in flags |
| `oidc_providers` | Registered OIDC IdP configurations (client ID, secret, issuer) |

### Auth flow

1. On first startup with no users, `ADMIN_EMAIL`/`ADMIN_PASSWORD` bootstrap the first admin account.
2. `POST /auth/login` validates password (bcrypt) **or** delegates to LDAP when `LDAP_HOST` is set. Checks account active state, handles TOTP if enabled, issues a JWT in a `gm_session` HttpOnly cookie (8-hour TTL). Returns `{"totp_required": true}` if 2FA is enabled and `totp_code` is not provided.
3. `GET /auth/oidc/{provider_id}/start` initiates OIDC authorization-code flow. After IdP redirect, `/callback` exchanges the code, validates the ID token, and provisions or updates the user account. The same session cookie is issued.
4. All `/api/*` routes require the cookie or an `Authorization: Bearer <api-key>` header.
5. Role-based access: `viewer` (read-only), `operator` (start/cancel jobs), `admin` (user management), `super_admin` (org management, all admin actions).
6. API keys are SHA-256 hashed in the database; the raw key is returned once on creation.

## Plugin SDK

Third-party connectors can be installed as Python packages. Implement `SourceConnector` or `DestinationConnector` from `groupware_migrator.sdk` and register via setuptools entry points:

```toml
[project.entry-points."groupware_migrator.source_connectors"]
myproto = "my_package.connector:MySourceConnector"

[project.entry-points."groupware_migrator.destination_connectors"]
myproto = "my_package.connector:MyDestinationConnector"
```

After `pip install my-package`, specify `"protocol": "myproto"` in migration configs. See `examples/sample_plugin/` for a complete working example. Installed plugins are listed at `GET /api/admin/plugins`.

## Notes

- POP3 is source-only; POP3 destination is out of scope.
- `tasks` (VTODO) and `notes` (VJOURNAL) workloads are fully supported over CalDAV.
- Job snapshots store credentials redacted; scheduled jobs store the full request (Fernet-encrypted if `VAULT_KEY` is set).
- The `data/state.db` file is created automatically on first startup. Switch to PostgreSQL by setting `DATABASE_URL`.
- LDAP / Active Directory bind is enabled by setting `LDAP_HOST`. It coexists with local password auth.
- OIDC SSO is configured via the admin UI (`/admin`) or `POST /api/admin/oidc/providers`. SAML 2.0 is not implemented — OIDC covers the majority of enterprise IdPs.
- The MS Graph connector requires an Entra ID app registration with `Mail.Read` (or `Mail.ReadBasic`) permissions and an OAuth2 access token passed as `source_oauth_access_token`.
