# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, with dev extras)
pip install -e ".[dev]"

# Run all tests
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Run a single test file
PYTHONPATH=src python3 -m unittest tests/test_runner.py -v

# Lint
ruff check src tests

# Start web UI (auto-creates venv, installs, launches uvicorn)
./start.sh

# Run tests via start.sh
./start.sh --test

# Start web UI manually (no reload)
uvicorn groupware_migrator.api.app:create_app --factory --reload

# CLI usage
groupware-migrator --config examples/migration-config.example.json --plan-only
groupware-migrator --config /path/to/migration.json --dry-run
groupware-migrator --config /path/to/migration.json --resume-job-id <job_id>
```

## Architecture

**Entry points**
- `cli.py` — CLI entrypoint; parses config JSON and invokes the runner directly.
- `api/app.py` — FastAPI app factory; mounts all routes and serves the static dashboard.

**Domain layer** (`models/domain.py`)
All core types live here as `@dataclass(slots=True)`:
- `MigrationRequest` — top-level request; holds `SourceEndpoint`, `DestinationEndpoint`, `WorkloadType`, `folder_mapping`, and `MigrationOptions`.
- `WorkloadType` — `mail | calendar | contacts | tasks | notes` (tasks/notes deferred from execution).
- Protocol enums: `SourceProtocol` (imap/pop3/caldav/carddav) and `DestinationProtocol` (imap/caldav/carddav).
- Workload–protocol validation is enforced at parse time in `_validate_workload_protocols`.
- All public types use dual-field aliases (`collection`/`mailbox`, `items`/`messages`) to stay backward-compatible with mail-only integrations.

**Connector layer** (`connectors/`)
- `base.py` — Abstract `SourceConnector` / `DestinationConnector`. Mail-specific methods (`list_mailboxes`, `iter_messages`, `append_message`, `ensure_mailbox`) are the required implementation surface; generic `list_collections`, `iter_items`, `upsert_item`, `ensure_collection` delegate to them by default so new mail connectors get collection semantics for free.
- `factory.py` — `create_source_connector` / `create_destination_connector` dispatch by protocol enum.
- `dav.py` — CalDAV and CardDAV connectors using raw HTTP (PROPFIND/GET/PUT/MKCOL); supports password and OAuth bearer flows.
- `imap.py`, `pop3.py` — IMAP (source+destination) and POP3 (source only).
- `auth.py` — OAuth token resolution (direct token or refresh-token exchange).

**Engine layer** (`engine/`)
- `planner.py` — Builds a `MigrationPlan` from collection snapshots and optional incremental cursors.
- `preflight.py` — Validates source/destination connectivity, plan generation, and incremental cursor metadata.
- `runner.py` — `MigrationRunner`; processes each plan item: source iteration → idempotency fingerprint → destination upsert/append → checkpoint write. Audit events logged at each step.
- `idempotency.py` — Fingerprint builders; skips items already migrated.
- `state.py` — `SQLiteStateStore`; all persistence (jobs, plans, checkpoints, sync cursors, audit events, batch rows) goes through here. Thread-safe via `threading.Lock`.
- `background.py` — Runs jobs in background threads; tracks live job state.
- `batch.py` — CSV parsing; merges per-row overrides onto a base `MigrationRequest`.
- `reporting.py` — Structured audit event queries and JSON/CSV report export.

**Sync/cursor model**
Incremental sync uses a `sync_cursors` table keyed by a stable identity hash (workload + source/destination host/user/protocol + collection filters). The runner writes cursor positions after each successful non-dry incremental job. `options.incremental_base_job_id` lets a new job anchor cursors to a previously completed job's checkpoints.

**API/UI contract**
- All API responses include both `collections`/`mailboxes` and `total_estimated_items`/`total_estimated_messages` aliases.
- SSE streams (`/api/jobs/stream`, `/api/jobs/{id}/stream`, `/api/batches/stream`, `/api/batches/{id}/stream`) replace polling.
- The static dashboard (`api/static/`) consumes these streams via `EventSource`.

**Provider presets** (`providers.py`)
Catalog of known providers (Gmail, Microsoft 365, Yahoo, Zoho) with default host/port/SSL/TLS/auth-mode values and OAuth token URL/scope hints.

## Commits & Versioning

- Automatic checkpoints use `wip:` prefix (Stop hook) — these don't trigger releases
- When asked to commit a feature/fix properly, use Conventional Commits:
  - `feat: description` → triggers minor release (0.x.0)
  - `fix: description` → triggers patch release (0.0.x)
  - `feat!: description` or add `BREAKING CHANGE:` in body → major release (x.0.0)
- Never use `--no-verify`
- Never push tags manually — CI handles that
