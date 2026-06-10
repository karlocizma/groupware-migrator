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
| `POST` | `/auth/login` | Log in; sets `gm_session` cookie |
| `POST` | `/auth/logout` | Clear session cookie |
| `GET` | `/auth/me` | Current user info |
| `POST` | `/auth/users` | Create a user (admin only) |
| `GET` | `/auth/users` | List users (admin only) |
| `POST` | `/auth/keys` | Create an API key |
| `GET` | `/auth/keys` | List your API keys |
| `DELETE` | `/auth/keys/{key_id}` | Revoke an API key |

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
│   ├── app.py              # FastAPI factory, lifespan, route mounting
│   ├── auth.py             # JWT creation/validation, FastAPI Depends
│   ├── schemas.py          # Pydantic request models
│   ├── routers/
│   │   ├── auth_router.py  # /auth/* endpoints
│   │   ├── jobs.py         # /jobs/* endpoints
│   │   ├── batches.py      # /batches/* endpoints
│   │   └── providers.py    # /providers endpoint
│   └── static/
│       ├── index.html
│       ├── login.html
│       ├── styles.css
│       └── js/
│           ├── main.js     # App entry point (ES module)
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
│   ├── background.py       # BackgroundJobManager (ThreadPoolExecutor)
│   ├── runner.py           # MigrationRunner; item iteration → upsert
│   ├── planner.py          # MigrationPlan construction
│   ├── preflight.py        # Connectivity + plan validation
│   ├── idempotency.py      # Fingerprint-based duplicate detection
│   ├── batch.py            # CSV parsing + batch row building
│   └── reporting.py        # Audit event queries, JSON/CSV export
├── models/
│   └── domain.py           # MigrationRequest, MigrationPlan, enums
├── providers.py            # Provider preset catalog
└── cli.py                  # CLI entrypoint
```

### State store (SQLite)

All persistence goes through `SQLiteStateStore`. Key tables:

| Table | Purpose |
|---|---|
| `users` | User accounts with bcrypt password hashes |
| `api_keys` | Hashed API keys per user |
| `jobs` | Migration jobs with status, counters, `user_id` |
| `batches` | Batch waves with summary counters, `user_id` |
| `batch_items` | Per-row job references within a batch |
| `checkpoints` | Per-job mailbox resume positions |
| `sync_cursors` | Persisted incremental sync positions (keyed by identity hash) |
| `message_migrations` | Fingerprint ledger for idempotency |
| `audit_events` | Structured per-job event log |

### Auth flow

1. On first startup, if no users exist and `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set, an admin account is created.
2. `POST /auth/login` validates password (bcrypt), issues a JWT in a `gm_session` HttpOnly cookie (8-hour TTL by default).
3. All `/api/*` routes require the cookie or a `Authorization: Bearer <api-key>` header.
4. Non-admin users see only their own jobs and batches; admins see all.
5. API keys are SHA-256 hashed in the database; the raw key is returned once on creation.

## Notes

- POP3 is source-only (destination POP3 is out of scope).
- `tasks` and `notes` workloads are modeled but not executed.
- Credentials are not persisted in plaintext — job snapshots are stored redacted.
- The `data/state.db` file is created automatically on first startup.
