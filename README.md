# Groupware Migrator
Local-first migration tool for mail, calendar, and contacts migration with an extensible protocol adapter architecture.
## MVP scope
- Source protocols: IMAP, POP3, CalDAV, CardDAV
- Destination protocols: IMAP, CalDAV, CardDAV
- Core capabilities:
  - Workload-aware migration for `mail`, `calendar`, and `contacts` (with `tasks`/`notes` intentionally deferred)
  - Connection validation
  - Migration planning
  - Asynchronous/background migration execution
  - CSV-driven batch migration for multi-user waves
  - Preflight checks for single jobs and CSV batches
  - Tabbed dashboard modes for single-user vs multi-user migration flows
  - Collapsible advanced settings for optional transport and migration controls
  - Password and OAuth/XOAUTH2 authentication modes for IMAP/POP3/CalDAV/CardDAV connectors
  - Full and incremental sync modes with persisted mailbox cursors
  - Checkpointed/resumable jobs
  - Idempotent item migration (duplicate prevention; mailbox/message aliases preserved for mail compatibility)
  - SQLite-backed migration state
  - Structured audit events and exportable job reports (JSON/CSV)
  - Provider presets with auth guidance and TLS profile controls
  - Modern web UI for plan creation and live job tracking over SSE
## Current milestone features
- Workload expansion (calendar + contacts):
  - CalDAV and CardDAV source/destination connectors
  - Collection/item planning and execution model with backward-compatible mailbox/message aliases
  - Workload selection across API/UI (`mail`, `calendar`, `contacts`)
  - Provider presets include CalDAV/CardDAV endpoint defaults where available
- Rich IMAP folder handling:
  - LIST response parsing with delimiter discovery
  - `\\Noselect` folders excluded from migration plans
  - Delimiter-aware destination mailbox creation
  - TLS profile selection (`modern`, `compatibility`) for IMAP/POP3
- Structured audit/reporting:
  - Per-job audit events persisted in SQLite (`audit_events`)
  - Event APIs for recent operational trail
  - Full report export APIs (JSON or downloadable CSV)
- Live updates via Server-Sent Events:
  - Job list stream endpoint
  - Selected-job detail stream endpoint
  - Dashboard EventSource clients (replaces interval polling loop)
- CSV batch migration:
  - Preview CSV validation before launch
  - Start migration waves with one row per user/account pair
  - Track batch-level status + row/job status with SSE
  - Allow strict mode (all rows valid) or partial mode (start only valid rows)
- Preflight checks:
  - Single-job preflight validates source + destination and attempts plan generation
  - Batch preflight runs row-level checks (configurable limit) and reports pass/fail counts
  - Dashboard shows per-check status, warnings, and row-level batch preflight badges
- Provider presets and auth guidance:
  - Presets: custom, Gmail, Microsoft 365, Yahoo, Zoho
  - Auto-fill defaults for host/port/SSL/TLS profile/auth mode
  - Provider-specific auth guidance with OAuth token endpoint/scope hints
- OAuth/XOAUTH2 auth mode:
  - `auth_mode` supported in connection payloads (`password` or `oauth2`)
  - XOAUTH2 used for IMAP source/destination and POP3 source when `oauth2` is selected
  - Supports direct `oauth_access_token` or refresh-token exchange (`oauth_refresh_token` + client credentials + token URL)
- Delta/incremental sync mode:
  - `options.sync_mode` supports `full` and `incremental`; `options.incremental_base_job_id` can anchor cursors to a completed base job
  - Persistent cursor state is stored per migration identity in SQLite (`sync_cursors`) and can also resolve from base job checkpoints
  - Planner/preflight consume resolved cursors and use connector pending-estimate hooks for IMAP/POP3 mailbox sizing
  - Runner starts each mailbox from resolved cursor/checkpoint and updates durable sync cursors after successful non-dry incremental jobs
  - Dashboard advanced settings include sync mode + optional base job ID, and preflight now displays incremental resolution metadata
  - Batch CSV parser supports row-level incremental overrides (`sync_mode`, `incremental_base_job_id`)
- Dashboard UX cleanup:
  - Mode tabs split the form into `Single User Migration` and `Multiple Users (Batch CSV)`
  - Non-essential fields are grouped under `Show advanced settings` (single mode)
  - Batch-only optional controls are grouped under `Show batch advanced settings`
## Project layout
- `src/groupware_migrator/connectors/`: protocol adapters (IMAP, POP3, CalDAV, CardDAV)
- `src/groupware_migrator/engine/`: planner, preflight checks, runner, idempotency, state store, background manager, reporting, batch CSV parsing
- `src/groupware_migrator/models/`: domain models
- `src/groupware_migrator/providers.py`: provider preset catalog and auth guidance
- `src/groupware_migrator/api/`: FastAPI service and static UI
- `src/groupware_migrator/api/static/`: dashboard UI assets (HTML/CSS/JS)
- `src/groupware_migrator/cli.py`: CLI entrypoint
- `tests/`: unit tests
## Architecture knowledgebase
- Workload model:
  - Requests now include a `workload` dimension and support `mail`, `calendar`, and `contacts`.
  - Protocol-workload pairing is validated in the domain layer (`mail` => IMAP/POP3 -> IMAP, `calendar` => CalDAV -> CalDAV, `contacts` => CardDAV -> CardDAV).
  - `tasks` and `notes` types remain modeled but intentionally deferred from active migration execution.
- Unified collection/item abstractions:
  - Planning and execution operate on generic `collection` and `item` concepts for all workloads.
  - Mail compatibility is preserved through aliases (`mailbox`/`message`) in request parsing and serialized responses.
  - `MigrationPlanItem` supports both new and legacy field names to avoid breaking existing mail tests/integrations.
- Connector architecture:
  - Source connectors expose listing and iteration via collection/item contracts with message wrappers for mail.
  - Destination connectors expose generic ensure/upsert operations with append wrappers for mail compatibility.
  - CalDAV/CardDAV connectors use WebDAV methods (PROPFIND/GET/PUT/MKCOL) and support both password auth and OAuth bearer flows.
- Execution pipeline:
  - Planner builds workload-aware migration plans using collection snapshots and optional incremental cursors.
  - Runner processes source items through idempotency fingerprinting, destination upsert/appends, and checkpoint updates.
  - Preflight validates source/destination connectivity, plan generation, and incremental cursor resolution metadata.
  - State persistence stores workload-aware sync identity and cursor/checkpoint data while keeping mail aliases in public payloads.
- API/UI contract:
  - API responses expose `workload`, `collections`, and `total_estimated_items` with legacy mailbox/message summary aliases.
  - UI supports workload selection, protocol auto-alignment for calendar/contacts, collection-oriented plan/preflight rendering, and unchanged mail flows.
  - Batch parsing supports workload/protocol overrides and collection-based alias fields (`collection_mapping`/`folder_mapping`, `source_include_collections`/`source_include_mailboxes`).
## Quick start
1. Create and activate a virtual environment.
2. Install the project:
   - `pip install -e .`
3. Build a migration plan in CLI (example):
   - `groupware-migrator --config examples/migration-config.example.json --plan-only`
4. Start the web UI:
   - `uvicorn groupware_migrator.api.app:create_app --factory --reload`
   - Open `http://127.0.0.1:8000`
## CLI usage
```bash path=null start=null
groupware-migrator --config /path/to/migration.json
groupware-migrator --config /path/to/migration.json --dry-run
groupware-migrator --config /path/to/migration.json --resume-job-id <job_id>
groupware-migrator --config /path/to/migration.json --state-db /path/to/state.db
```
## Run tests
```bash path=null start=null
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
## API endpoints
- `GET /api/providers` - list provider presets, defaults, and auth guidance.
- `POST /api/jobs/preflight` - validate source/destination connectivity and plan readiness (includes incremental cursor metadata when enabled).
- `POST /api/batches/preview` - validate CSV rows against a base request template.
- `POST /api/batches/preflight` - run limited row-level preflight checks for batch rows.
- `POST /api/batches/start` - create and start a batch wave from CSV rows.
- `GET /api/batches` - list recent batch waves.
- `GET /api/batches/{batch_id}` - get batch summary + row-level status.
- `GET /api/batches/stream` - SSE stream for recent batch summaries.
- `GET /api/batches/{batch_id}/stream` - SSE stream for one selected batch.
- `POST /api/jobs/plan` - build migration plan from request payload.
- `POST /api/jobs/start` - create and run a job in background.
- `POST /api/jobs/run` - run synchronously.
- `POST /api/jobs/resume` - resume an existing job in background (requires `job_id` + request payload).
- `GET /api/jobs` - list recent jobs.
- `GET /api/jobs/{job_id}` - get full job details.
- `GET /api/jobs/{job_id}/events` - list structured audit events.
- `GET /api/jobs/{job_id}/report?format=json|csv` - export a full job report.
- `GET /api/jobs/stream` - SSE stream of recent jobs.
- `GET /api/jobs/{job_id}/stream` - SSE stream for selected job detail.
## CSV batch format
- Each CSV row represents one migration job.
- Recommended minimal columns:
  - `job_name`
  - `source_username`
  - `source_password`
  - `destination_username`
  - `destination_password`
- Optional override columns (examples): `workload`, `source_protocol`, `destination_protocol`, `source_host`, `source_port`, `destination_host`, `destination_port`, `source_include_collections` (alias: `source_include_mailboxes`), `destination_root_collection` (alias: `destination_root_mailbox`), `collection_mapping` (alias: `folder_mapping`), `dry_run`, `max_errors`.
- Incremental override columns:
  - `sync_mode` (`full` or `incremental`)
  - `incremental_base_job_id` (completed job ID to use as incremental baseline)
- OAuth/auth override columns are also supported, including:
  - `source_auth_mode`, `source_oauth_access_token`, `source_oauth_refresh_token`, `source_oauth_client_id`, `source_oauth_client_secret`, `source_oauth_token_url`, `source_oauth_scope`
  - `destination_auth_mode`, `destination_oauth_access_token`, `destination_oauth_refresh_token`, `destination_oauth_client_id`, `destination_oauth_client_secret`, `destination_oauth_token_url`, `destination_oauth_scope`
- The dashboard sends current form values as `base_request`, and CSV columns override per-row fields.
- Sample file: `examples/batch-users.example.csv`
## Dashboard workflow
- Choose one mode at the top of the form:
  - `Single User Migration` for one account pair and job-level actions (`Run Preflight`, `Build Plan`, `Start Background Job`)
  - `Multiple Users (Batch CSV)` for CSV preview/preflight/start batch actions
- Choose `workload` (`mail`, `calendar`, or `contacts`) before filling connection fields. Calendar/contacts automatically pin compatible protocol choices.
- Fill required connection and credential fields in the default form surface.
- Open advanced panels only when needed:
  - Single mode advanced panel contains auth mode, OAuth token/client fields, TLS profile, SSL toggles, include-mailboxes, destination root, POP3 destination mailbox, folder mapping, max-errors, and dry-run.
  - Single mode advanced panel also contains sync mode and optional incremental base job ID.
  - Batch advanced panel contains `allow partial` behavior and `batch preflight row limit`.
- Provider presets continue to set host/port/SSL/TLS/auth-mode defaults (plus OAuth token URL/scope hints) even when advanced fields are collapsed.
- In OAuth mode:
  - Provide either `OAuth access token` directly, or a refresh flow (`refresh token`, `client ID`, `client secret`, and `token URL`).
  - Password fields are not used for auth execution.
## Notes
- POP3 is supported as a source protocol only (destination POP3 is intentionally out of scope).
- Current workload implementation priority is mail + calendar + contacts; tasks and notes remain deferred.
- Credentials are not persisted in plaintext job metadata (job request snapshots are stored redacted).
