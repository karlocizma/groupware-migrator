# Groupware Migrator

Local-first tool for migrating email, calendar, and contacts between servers. Runs as a web application or CLI. Supports IMAP, POP3, CalDAV, and CardDAV with resumable background jobs, CSV batch migration, and a live-streaming dashboard.

## Features

- **Workloads:** `mail` (IMAP/POP3 → IMAP), `calendar` (CalDAV → CalDAV), `contacts` (CardDAV → CardDAV)
- **Background jobs:** asynchronous execution with live SSE progress updates
- **Batch migration:** CSV-driven multi-user waves with per-row overrides
- **Incremental sync:** cursor-based delta sync; anchored to a completed base job or persisted cursors
- **Preflight checks:** validates source/destination connectivity and plan readiness before execution
- **Idempotency:** fingerprint-based duplicate prevention across runs
- **Auth modes:** password and OAuth/XOAUTH2 (direct access token or refresh-token exchange)
- **Provider presets:** Gmail, Microsoft 365, Yahoo, Zoho — auto-fill host/port/TLS/auth defaults
- **Multi-user:** JWT session authentication, per-user job scoping, API key support
- **Reports:** per-job structured audit events, JSON/CSV export
- **Scheduling:** cron-style and interval-based recurring jobs (e.g. `0 2 * * *`, `6h`)
- **Webhooks:** HMAC-SHA256-signed POST notifications on job completion/failure
- **2FA:** TOTP two-factor authentication with recovery codes (Google Authenticator / Authy)
- **RBAC:** four roles — `viewer`, `operator`, `admin`, `super_admin`
- **Organizations:** group users into workspaces with owner/admin/member roles
- **Credential vault:** optional AES encryption for scheduled job credentials (`VAULT_KEY`)

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

### Environment variables

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

All `/api/*` endpoints require authentication (JWT cookie or `Authorization: Bearer <api-key>`).

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
| `POST` | `/api/admin/smtp/test` | Send a test email to verify SMTP config (admin only) |
| `GET` | `/api/admin/ldap/status` | Check whether LDAP is configured (admin only) |
| `GET` | `/api/admin/plugins` | List installed connector plugins (admin only) |

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

All `/api/*` endpoints are also accessible at `/api/v1/*` (versioned prefix).

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
│   ├── routers/
│   │   ├── auth_router.py  # /auth/* (login, TOTP, API keys, user management)
│   │   ├── admin_router.py # /admin/* (stats, users, audit log, cleanup)
│   │   ├── jobs.py         # /jobs/* endpoints
│   │   ├── batches.py      # /batches/* endpoints
│   │   ├── scheduler_router.py  # /schedules/* CRUD
│   │   ├── webhooks_router.py   # /webhooks/* CRUD + delivery log
│   │   ├── orgs_router.py       # /orgs/* CRUD + member management
│   │   └── providers.py    # /providers endpoint
│   └── static/
│       ├── index.html      # Main dashboard
│       ├── login.html      # Login page (TOTP step-up aware)
│       ├── admin.html      # Admin dashboard
│       ├── scheduler.html  # Schedules, webhooks, 2FA settings
│       ├── orgs.html       # Organization management
│       ├── styles.css
│       └── js/
│           ├── main.js     # Dashboard entry point (ES module)
│           ├── admin.js    # Admin panel logic
│           ├── scheduler.js # Schedules/webhooks/TOTP logic
│           ├── api.js      # Fetch wrapper with 401 redirect
│           └── form.js     # localStorage form persistence
├── connectors/
│   ├── imap.py             # IMAP source + destination
│   ├── pop3.py             # POP3 source
│   ├── dav.py              # CalDAV + CardDAV (PROPFIND/GET/PUT/MKCOL)
│   ├── auth.py             # OAuth token resolution
│   └── factory.py          # Protocol → connector dispatch
├── engine/
│   ├── state.py            # SQLiteStateStore; all persistence
│   ├── background.py       # BackgroundJobManager (ThreadPoolExecutor + webhooks)
│   ├── runner.py           # MigrationRunner; item iteration → upsert
│   ├── scheduler.py        # SchedulerThread; fires due cron/interval schedules
│   ├── cron.py             # Minimal 5-field cron expression parser
│   ├── webhooks.py         # WebhookDeliveryManager; HMAC-signed HTTP POST
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

### State store (SQLite)

All persistence goes through `SQLiteStateStore`. Key tables:

| Table | Purpose |
|---|---|
| `users` | Accounts with bcrypt hashes, `role`, `is_active`, TOTP columns |
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

### Auth flow

1. On first startup with no users, `ADMIN_EMAIL`/`ADMIN_PASSWORD` bootstrap the first admin account.
2. `POST /auth/login` validates password (bcrypt), checks if account is active, handles TOTP if enabled, and issues a JWT in a `gm_session` HttpOnly cookie (8-hour TTL by default). Returns `{"totp_required": true}` if 2FA is enabled and `totp_code` is not provided.
3. All `/api/*` routes require the cookie or an `Authorization: Bearer <api-key>` header.
4. Role-based access: `viewer` (read-only), `operator` (start/cancel jobs), `admin` (user management), `super_admin` (org management, all admin actions).
5. API keys are SHA-256 hashed in the database; the raw key is returned once on creation.

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

- POP3 is source-only (destination POP3 is out of scope).
- `tasks` and `notes` workloads are modeled but not executed.
- Job snapshots store credentials redacted; scheduled jobs store the full request (Fernet-encrypted if `VAULT_KEY` is set).
- The `data/state.db` file is created automatically on first startup.
- SAML/OIDC SSO is not implemented — this requires an external IdP (Okta, Auth0, Keycloak) and is left for a future integration phase.
- LDAP / Active Directory bind is implemented; set `LDAP_HOST` to enable it alongside the built-in password auth.
