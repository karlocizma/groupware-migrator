# Phase 1 — Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up six structural backend problems so the codebase is a solid foundation for Phase 2 (auth).

**Architecture:** Five sequential tasks — deduplicate a function, add crash recovery via a lifespan context manager, add structured logging, split the 640-line `app.py` into three FastAPI routers, and add Pydantic request models. Each task is independently committable and leaves all existing tests passing.

**Tech Stack:** Python 3.11+, FastAPI ≥ 0.115, Pydantic v2 (bundled with FastAPI), Python `logging` stdlib, SQLite via `sqlite3` stdlib.

---

## File Map

**New files:**
- `src/groupware_migrator/api/routers/__init__.py` — empty package marker
- `src/groupware_migrator/api/routers/providers.py` — `/api/providers` endpoint
- `src/groupware_migrator/api/routers/jobs.py` — all `/api/jobs/*` endpoints + helpers
- `src/groupware_migrator/api/routers/batches.py` — all `/api/batches/*` endpoints + helpers
- `src/groupware_migrator/api/schemas.py` — Pydantic request models

**Modified files:**
- `src/groupware_migrator/engine/state.py` — rename `_derive_batch_status` → `derive_batch_status` (public); add `recover_stuck_jobs()`
- `src/groupware_migrator/api/app.py` — remove duplicate; add `_configure_logging()`; add lifespan; wire routers
- `src/groupware_migrator/engine/runner.py` — add `logger`
- `src/groupware_migrator/engine/preflight.py` — add `logger`
- `src/groupware_migrator/connectors/imap.py` — add `logger`
- `src/groupware_migrator/connectors/dav.py` — add `logger`
- `src/groupware_migrator/connectors/pop3.py` — add `logger`

**Test files (modified):**
- `tests/test_state.py` — add `test_recover_stuck_jobs`

---

## Task 1: Remove duplicated `_batch_status_from_counts`

`_derive_batch_status` in `state.py` and `_batch_status_from_counts` in `app.py` are identical. Make the state.py version public and delete the app.py copy.

**Files:**
- Modify: `src/groupware_migrator/engine/state.py:18`
- Modify: `src/groupware_migrator/engine/state.py:696`
- Modify: `src/groupware_migrator/api/app.py:78-94` (delete) and `:171` (update call)

- [ ] **Step 1: Rename `_derive_batch_status` to `derive_batch_status` in `state.py`**

  In `src/groupware_migrator/engine/state.py`, change line 18:

  ```python
  # Before
  def _derive_batch_status(
  # After
  def derive_batch_status(
  ```

  And update the call at line 696:

  ```python
  # Before
          status = _derive_batch_status(
  # After
          status = derive_batch_status(
  ```

- [ ] **Step 2: Delete `_batch_status_from_counts` from `app.py` and update its one call site**

  Delete lines 78–94 of `src/groupware_migrator/api/app.py` (the entire `_batch_status_from_counts` function).

  At the top of `app.py`, add the import:

  ```python
  from groupware_migrator.engine.state import SQLiteStateStore, derive_batch_status
  ```

  (Replace the existing `from groupware_migrator.engine.state import SQLiteStateStore` line.)

  In `_batch_response` (the `items is not None` branch, around old line 171), change:

  ```python
  # Before
                  "status": _batch_status_from_counts(
  # After
                  "status": derive_batch_status(
  ```

- [ ] **Step 3: Run the full test suite — expect all tests to pass**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v
  ```

  Expected: all tests pass. If any test imports `_batch_status_from_counts` directly, update it to `derive_batch_status`.

- [ ] **Step 4: Commit**

  ```bash
  git add src/groupware_migrator/engine/state.py src/groupware_migrator/api/app.py
  git commit -m "refactor: consolidate batch status function in state module"
  ```

---

## Task 2: Crash recovery + lifespan context manager

Two related concerns: (a) jobs stuck in `running` state after a crash should be marked `failed` on startup; (b) the deprecated `@app.on_event("shutdown")` should become an `@asynccontextmanager` lifespan. Both land in the same lifespan function.

**Files:**
- Modify: `src/groupware_migrator/engine/state.py` — add `recover_stuck_jobs()`
- Modify: `src/groupware_migrator/api/app.py` — replace `@app.on_event` with lifespan
- Modify: `tests/test_state.py` — add crash recovery test

- [ ] **Step 1: Write the failing test in `tests/test_state.py`**

  Add this test class to `tests/test_state.py`:

  ```python
  class TestRecoverStuckJobs(unittest.TestCase):
      def _make_store(self, temp_dir: str) -> SQLiteStateStore:
          return SQLiteStateStore(Path(temp_dir) / "state.db")

      def _make_request(self) -> MigrationRequest:
          return MigrationRequest.from_dict({
              "source": {
                  "protocol": "imap",
                  "connection": {"host": "src", "username": "u", "password": "p"},
              },
              "destination": {
                  "protocol": "imap",
                  "connection": {"host": "dst", "username": "u", "password": "p"},
              },
          })

      def test_recover_stuck_jobs_marks_running_as_failed(self):
          with tempfile.TemporaryDirectory() as tmp:
              store = self._make_store(tmp)
              request = self._make_request()
              plan = MigrationPlan()

              # Create a job and manually force it into running state
              job_id = store.create_job(request, plan)
              store.set_job_status(job_id, JobStatus.RUNNING, set_started=True)

              recovered = store.recover_stuck_jobs()

              self.assertEqual(recovered, 1)
              job = store.get_job(job_id)
              self.assertEqual(job["status"], JobStatus.FAILED.value)
              self.assertIsNotNone(job["last_error"])
              self.assertIsNotNone(job["finished_at"])

      def test_recover_stuck_jobs_ignores_completed_and_pending(self):
          with tempfile.TemporaryDirectory() as tmp:
              store = self._make_store(tmp)
              request = self._make_request()

              job_pending = store.create_job(request, MigrationPlan())
              job_completed = store.create_job(request, MigrationPlan())
              store.set_job_status(job_completed, JobStatus.COMPLETED, set_finished=True)

              recovered = store.recover_stuck_jobs()

              self.assertEqual(recovered, 0)
              self.assertEqual(store.get_job(job_pending)["status"], JobStatus.PENDING.value)
              self.assertEqual(store.get_job(job_completed)["status"], JobStatus.COMPLETED.value)
  ```

- [ ] **Step 2: Run the new tests — expect FAIL (method doesn't exist yet)**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_state.TestRecoverStuckJobs -v
  ```

  Expected: `AttributeError: 'SQLiteStateStore' object has no attribute 'recover_stuck_jobs'`

- [ ] **Step 3: Implement `recover_stuck_jobs()` in `state.py`**

  Add this method to `SQLiteStateStore`, after `set_job_status`:

  ```python
  def recover_stuck_jobs(self) -> int:
      """Mark jobs stuck in running state as failed. Returns count of recovered jobs."""
      with self._lock, self._connection() as connection:
          cursor = connection.execute(
              """
              UPDATE jobs
              SET status = ?,
                  last_error = ?,
                  finished_at = ?,
                  updated_at = ?
              WHERE status = ?
              """,
              (
                  JobStatus.FAILED.value,
                  "Server restarted while job was running.",
                  _utcnow_iso(),
                  _utcnow_iso(),
                  JobStatus.RUNNING.value,
              ),
          )
          return cursor.rowcount
  ```

- [ ] **Step 4: Run the new tests — expect PASS**

  ```bash
  PYTHONPATH=src python3 -m unittest tests.test_state.TestRecoverStuckJobs -v
  ```

  Expected: 2 tests pass.

- [ ] **Step 5: Replace `@app.on_event` with a lifespan in `app.py`**

  At the top of `src/groupware_migrator/api/app.py`, add:

  ```python
  import logging
  from contextlib import asynccontextmanager
  ```

  Inside `create_app()`, replace this block:

  ```python
  # DELETE these lines:
  @app.on_event("shutdown")
  def shutdown_background_workers() -> None:
      background_jobs.shutdown(wait=False)
  ```

  With a lifespan defined **before** the `FastAPI(...)` call. Restructure `create_app` so it reads:

  ```python
  def create_app(*, state_db_path: str = "data/state.db") -> FastAPI:
      state_store = SQLiteStateStore(Path(state_db_path))
      runner = MigrationRunner(state_store=state_store)
      background_jobs = BackgroundJobManager(
          state_store=state_store,
          runner=runner,
      )

      @asynccontextmanager
      async def lifespan(app: FastAPI):
          recovered = state_store.recover_stuck_jobs()
          if recovered:
              logging.getLogger(__name__).warning(
                  "Recovered %d job(s) stuck in running state on startup.", recovered
              )
          yield
          background_jobs.shutdown(wait=False)

      app = FastAPI(title="Groupware Migrator", version="0.3.0", lifespan=lifespan)
      # ... rest of the function unchanged
  ```

  Remove the old `@app.on_event("shutdown")` block entirely.

- [ ] **Step 6: Run the full test suite — expect all tests to pass**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add src/groupware_migrator/engine/state.py \
          src/groupware_migrator/api/app.py \
          tests/test_state.py
  git commit -m "feat: add crash recovery for stuck jobs; replace deprecated on_event with lifespan"
  ```

---

## Task 3: Structured logging

Add `logging.getLogger(__name__)` to the engine and connectors so production issues are diagnosable. Configure log level from `LOG_LEVEL` env var.

**Files:**
- Modify: `src/groupware_migrator/api/app.py` — add `_configure_logging()`
- Modify: `src/groupware_migrator/engine/runner.py` — add logger, log key events
- Modify: `src/groupware_migrator/engine/preflight.py` — add logger
- Modify: `src/groupware_migrator/connectors/imap.py` — add logger
- Modify: `src/groupware_migrator/connectors/dav.py` — add logger
- Modify: `src/groupware_migrator/connectors/pop3.py` — add logger

- [ ] **Step 1: Add `_configure_logging()` to `app.py` and call it at the top of `create_app()`**

  Add this function at module level in `src/groupware_migrator/api/app.py` (after imports):

  ```python
  import os

  def _configure_logging() -> None:
      level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
      level = getattr(logging, level_name, logging.INFO)
      logging.basicConfig(
          level=level,
          format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
          datefmt="%Y-%m-%dT%H:%M:%S",
      )
  ```

  Call it as the **first line** of `create_app()`:

  ```python
  def create_app(*, state_db_path: str = "data/state.db") -> FastAPI:
      _configure_logging()
      # ... rest unchanged
  ```

- [ ] **Step 2: Add a logger to `runner.py`**

  At the top of `src/groupware_migrator/engine/runner.py`, after the imports, add:

  ```python
  import logging

  logger = logging.getLogger(__name__)
  ```

  In the `_audit` method's `except Exception` clause, replace the silent `return` with a logged warning:

  ```python
  except Exception as exc:
      logger.warning("Failed to write audit event %s for job %s: %s", event_type, job_id, exc)
      return
  ```

  At the start of `run()`, after the `job_id` is resolved, add:

  ```python
  logger.info("Job %s starting (workload=%s, dry_run=%s)", job_id, request.workload.value, request.options.dry_run)
  ```

  In the outer `except Exception` block at the end of `run()`, add before `set_job_status`:

  ```python
  logger.error("Job %s failed: %s", job_id, error_messages[-1] if error_messages else str(exc))
  ```

  After `set_job_status(JobStatus.COMPLETED ...)`, add:

  ```python
  logger.info("Job %s completed (migrated=%d, skipped=%d, failed=%d)", job_id, ...)
  ```

  Use the `job_row` fetched just after to fill in counts:

  ```python
  job_row = self._state_store.get_job(job_id)
  if job_row:
      logger.info(
          "Job %s completed (migrated=%d, skipped=%d, failed=%d)",
          job_id,
          int(job_row["migrated_count"]),
          int(job_row["skipped_count"]),
          int(job_row["failed_count"]),
      )
      self._audit(job_id, "job_completed", payload={...})  # existing code
  ```

- [ ] **Step 3: Add a logger to `preflight.py`**

  At the top of `src/groupware_migrator/engine/preflight.py`, after imports:

  ```python
  import logging

  logger = logging.getLogger(__name__)
  ```

  In `run_preflight`, after the `except Exception as exc` block for source validation, add:

  ```python
  except Exception as exc:
      logger.debug("Preflight source validation failed: %s", exc)
      result["source"]["error"] = _exc_message(exc)
      result["plan"]["error"] = "Skipped because source connection validation failed."
  ```

  Do the same for destination:

  ```python
  except Exception as exc:
      logger.debug("Preflight destination validation failed: %s", exc)
      result["destination"]["error"] = _exc_message(exc)
  ```

- [ ] **Step 4: Add loggers to the connectors**

  In `src/groupware_migrator/connectors/imap.py`, after imports add:

  ```python
  import logging

  logger = logging.getLogger(__name__)
  ```

  In `src/groupware_migrator/connectors/dav.py`, after imports add:

  ```python
  import logging

  logger = logging.getLogger(__name__)
  ```

  In `src/groupware_migrator/connectors/pop3.py`, after imports add:

  ```python
  import logging

  logger = logging.getLogger(__name__)
  ```

  In each connector's `validate()` method, add a debug log on success:

  ```python
  # imap.py validate():
  logger.debug("IMAP source connection validated: %s@%s", self._config.username, self._config.host)

  # dav.py CalDavSourceConnector.validate():
  logger.debug("CalDAV source connection validated: %s@%s", self._config.username, self._config.host)

  # pop3.py validate():
  logger.debug("POP3 source connection validated: %s@%s", self._config.username, self._config.host)
  ```

- [ ] **Step 5: Run the full test suite — expect all tests to pass**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v
  ```

- [ ] **Step 6: Verify `LOG_LEVEL` env var works**

  ```bash
  LOG_LEVEL=DEBUG PYTHONPATH=src python3 -c "
  from groupware_migrator.api.app import create_app
  app = create_app()
  print('OK')
  "
  ```

  Expected: prints `OK` with no errors.

- [ ] **Step 7: Commit**

  ```bash
  git add src/groupware_migrator/api/app.py \
          src/groupware_migrator/engine/runner.py \
          src/groupware_migrator/engine/preflight.py \
          src/groupware_migrator/connectors/imap.py \
          src/groupware_migrator/connectors/dav.py \
          src/groupware_migrator/connectors/pop3.py
  git commit -m "feat: add structured logging with LOG_LEVEL env var support"
  ```

---

## Task 4: Split `app.py` into FastAPI routers

Extract the 640-line `create_app` factory into three focused router files. The routers receive shared state via factory functions (same pattern as `create_app` itself). `app.py` becomes a thin wiring module.

**Files:**
- Create: `src/groupware_migrator/api/routers/__init__.py`
- Create: `src/groupware_migrator/api/routers/providers.py`
- Create: `src/groupware_migrator/api/routers/jobs.py`
- Create: `src/groupware_migrator/api/routers/batches.py`
- Modify: `src/groupware_migrator/api/app.py`

- [ ] **Step 1: Create the routers package**

  Create `src/groupware_migrator/api/routers/__init__.py` as an empty file.

- [ ] **Step 2: Create `routers/providers.py`**

  Create `src/groupware_migrator/api/routers/providers.py`:

  ```python
  from __future__ import annotations

  from fastapi import APIRouter

  from groupware_migrator.providers import get_provider_presets


  def create_providers_router() -> APIRouter:
      router = APIRouter()

      @router.get("/providers")
      def list_providers() -> dict:
          return {"items": get_provider_presets()}

      return router
  ```

- [ ] **Step 3: Create `routers/jobs.py`**

  Create `src/groupware_migrator/api/routers/jobs.py` with all job-related endpoints and helpers. The router factory receives `state_store`, `background_jobs`, and `runner` as arguments:

  ```python
  from __future__ import annotations

  import asyncio
  import json

  from fastapi import APIRouter, HTTPException, Query, Request
  from fastapi.responses import PlainTextResponse, StreamingResponse

  from groupware_migrator.connectors.factory import create_source_connector
  from groupware_migrator.engine.background import BackgroundJobManager
  from groupware_migrator.engine.preflight import run_preflight
  from groupware_migrator.engine.reporting import build_job_report, build_job_report_csv
  from groupware_migrator.engine.runner import MigrationRunner
  from groupware_migrator.engine.state import SQLiteStateStore
  from groupware_migrator.models import MigrationPlan, MigrationRequest


  def _parse_json_blob(raw_payload: str) -> dict:
      if not raw_payload:
          return {}
      return json.loads(raw_payload)


  def _migration_request_from_payload(payload: dict) -> MigrationRequest:
      request_payload = payload.get("request", payload)
      return MigrationRequest.from_dict(request_payload)


  def _sse_message(event: str, payload: dict) -> str:
      encoded = json.dumps(payload, separators=(",", ":"))
      return f"event: {event}\ndata: {encoded}\n\n"


  def _job_response(job_row: dict, *, running: bool, include_payload: bool) -> dict:
      plan_payload = _parse_json_blob(job_row.get("plan_json", ""))
      request_payload = _parse_json_blob(job_row.get("request_json", ""))
      workload = str(request_payload.get("workload", "mail"))
      total_estimated_items = int(
          plan_payload.get(
              "total_estimated_items",
              plan_payload.get("total_estimated_messages", 0),
          )
      )
      collections = len(plan_payload.get("items", []))

      response = {
          "job_id": job_row["job_id"],
          "job_name": job_row["job_name"],
          "status": job_row["status"],
          "running": running,
          "workload": workload,
          "source_protocol": job_row["source_protocol"],
          "destination_protocol": job_row["destination_protocol"],
          "dry_run": bool(job_row["dry_run"]),
          "migrated_count": job_row["migrated_count"],
          "skipped_count": job_row["skipped_count"],
          "failed_count": job_row["failed_count"],
          "created_at": job_row["created_at"],
          "updated_at": job_row["updated_at"],
          "started_at": job_row["started_at"],
          "finished_at": job_row["finished_at"],
          "last_error": job_row["last_error"],
          "plan_summary": {
              "collections": collections,
              "total_estimated_items": total_estimated_items,
              "mailboxes": collections,
              "total_estimated_messages": total_estimated_items,
          },
      }
      if include_payload:
          response["request"] = request_payload
          response["plan"] = plan_payload
      return response


  def create_jobs_router(
      state_store: SQLiteStateStore,
      background_jobs: BackgroundJobManager,
      runner: MigrationRunner,
  ) -> APIRouter:
      router = APIRouter()

      @router.post("/jobs/preflight")
      def preflight_job(payload: dict) -> dict:
          try:
              request = _migration_request_from_payload(payload)
              return run_preflight(request, state_store=state_store)
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.post("/jobs/plan")
      def plan_job(payload: dict) -> dict:
          try:
              request = _migration_request_from_payload(payload)
              source_connector = create_source_connector(request)
              plan = runner.plan(request=request, source_connector=source_connector)
              return plan.to_dict()
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.post("/jobs/run")
      def run_job(payload: dict) -> dict:
          try:
              resume_job_id = payload.get("resume_job_id")
              request = _migration_request_from_payload(payload)
              report = runner.run(request=request, resume_job_id=resume_job_id)
              return report.to_dict()
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.post("/jobs/start")
      def start_background_job(payload: dict) -> dict:
          try:
              request = _migration_request_from_payload(payload)
              job_id = background_jobs.start_job(request=request)
              job_row = state_store.get_job(job_id)
              if not job_row:
                  raise RuntimeError("Unable to read background job after creation.")
              return _job_response(job_row, running=True, include_payload=True)
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.post("/jobs/resume")
      def resume_background_job(payload: dict) -> dict:
          try:
              if "job_id" not in payload:
                  raise ValueError("Missing required field: job_id.")
              job_id = str(payload["job_id"])
              request = _migration_request_from_payload(payload)
              background_jobs.resume_job(request=request, job_id=job_id)
              job_row = state_store.get_job(job_id)
              if not job_row:
                  raise RuntimeError(f"Unable to read resumed job {job_id}.")
              return _job_response(job_row, running=True, include_payload=True)
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.get("/jobs")
      def list_jobs(limit: int = Query(default=20, ge=1, le=200)) -> dict:
          rows = state_store.list_jobs(limit=limit)
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

      @router.get("/jobs/stream")
      async def jobs_stream(
          request: Request,
          limit: int = Query(default=30, ge=1, le=200),
      ) -> StreamingResponse:
          async def event_generator():
              yield "retry: 1500\n\n"
              while True:
                  if await request.is_disconnected():
                      break
                  rows = state_store.list_jobs(limit=limit)
                  payload = {
                      "items": [
                          _job_response(
                              row,
                              running=background_jobs.is_running(str(row["job_id"])),
                              include_payload=False,
                          )
                          for row in rows
                      ]
                  }
                  yield _sse_message("jobs", payload)
                  await asyncio.sleep(1.5)

          return StreamingResponse(event_generator(), media_type="text/event-stream")

      @router.get("/jobs/{job_id}")
      def get_job(job_id: str) -> dict:
          job_row = state_store.get_job(job_id)
          if not job_row:
              raise HTTPException(status_code=404, detail="Job not found.")
          return _job_response(job_row, running=background_jobs.is_running(job_id), include_payload=True)

      @router.get("/jobs/{job_id}/events")
      def get_job_events(
          job_id: str,
          limit: int = Query(default=200, ge=1, le=2000),
      ) -> dict:
          if not state_store.has_job(job_id):
              raise HTTPException(status_code=404, detail="Job not found.")
          return {"items": state_store.list_audit_events(job_id, limit=limit)}

      @router.get("/jobs/{job_id}/report")
      def get_job_report(
          job_id: str,
          format: str = Query(default="json"),
          audit_limit: int = Query(default=2000, ge=1, le=5000),
      ):
          if not state_store.has_job(job_id):
              raise HTTPException(status_code=404, detail="Job not found.")
          report_payload = build_job_report(
              state_store, job_id=job_id, audit_event_limit=audit_limit
          )
          if format.lower() == "csv":
              csv_payload = build_job_report_csv(report_payload)
              return PlainTextResponse(
                  csv_payload,
                  media_type="text/csv",
                  headers={"Content-Disposition": f"attachment; filename=job-{job_id}-report.csv"},
              )
          return report_payload

      @router.get("/jobs/{job_id}/stream")
      async def job_stream(job_id: str, request: Request) -> StreamingResponse:
          async def event_generator():
              yield "retry: 1500\n\n"
              while True:
                  if await request.is_disconnected():
                      break
                  row = state_store.get_job(job_id)
                  if not row:
                      yield _sse_message("error", {"error": "Job not found."})
                      break
                  yield _sse_message(
                      "job",
                      _job_response(row, running=background_jobs.is_running(job_id), include_payload=True),
                  )
                  await asyncio.sleep(1.0)

          return StreamingResponse(event_generator(), media_type="text/event-stream")

      return router
  ```

- [ ] **Step 4: Create `routers/batches.py`**

  Create `src/groupware_migrator/api/routers/batches.py`:

  ```python
  from __future__ import annotations

  import asyncio
  import json

  from fastapi import APIRouter, HTTPException, Query, Request
  from fastapi.responses import StreamingResponse

  from groupware_migrator.engine.background import BackgroundJobManager
  from groupware_migrator.engine.batch import build_batch_preview, build_batch_rows
  from groupware_migrator.engine.preflight import run_preflight
  from groupware_migrator.engine.state import SQLiteStateStore, derive_batch_status
  from groupware_migrator.models import JobStatus, MigrationPlan


  def _sse_message(event: str, payload: dict) -> str:
      encoded = json.dumps(payload, separators=(",", ":"))
      return f"event: {event}\ndata: {encoded}\n\n"


  def _batch_item_response(item_row: dict, *, running: bool) -> dict:
      status = str(item_row.get("status", JobStatus.PENDING.value))
      if running and status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
          status = JobStatus.RUNNING.value
      return {
          "row_number": int(item_row.get("row_number", 0)),
          "job_id": item_row.get("job_id"),
          "job_name": item_row.get("job_name"),
          "source_username": item_row.get("source_username"),
          "destination_username": item_row.get("destination_username"),
          "status": status,
          "running": running,
          "migrated_count": int(item_row.get("migrated_count", 0)),
          "skipped_count": int(item_row.get("skipped_count", 0)),
          "failed_count": int(item_row.get("failed_count", 0)),
          "last_error": item_row.get("last_error"),
          "created_at": item_row.get("created_at"),
          "updated_at": item_row.get("updated_at"),
          "started_at": item_row.get("started_at"),
          "finished_at": item_row.get("finished_at"),
      }


  def _batch_response(batch_row: dict, *, items: list[dict] | None = None) -> dict:
      response = {
          "batch_id": batch_row.get("batch_id"),
          "batch_name": batch_row.get("batch_name"),
          "status": batch_row.get("status"),
          "total_rows": int(batch_row.get("total_rows", 0)),
          "pending_rows": int(batch_row.get("pending_rows", 0)),
          "running_rows": int(batch_row.get("running_rows", 0)),
          "completed_rows": int(batch_row.get("completed_rows", 0)),
          "failed_rows": int(batch_row.get("failed_rows", 0)),
          "migrated_count": int(batch_row.get("migrated_count", 0)),
          "skipped_count": int(batch_row.get("skipped_count", 0)),
          "message_failed_count": int(batch_row.get("message_failed_count", 0)),
          "created_at": batch_row.get("created_at"),
          "updated_at": batch_row.get("updated_at"),
      }
      if items is not None:
          total_rows = max(response["total_rows"], len(items))
          pending_rows = running_rows = completed_rows = failed_rows = 0
          migrated_count = skipped_count = message_failed_count = 0
          for item in items:
              s = str(item.get("status", JobStatus.PENDING.value))
              if s == JobStatus.COMPLETED.value:
                  completed_rows += 1
              elif s == JobStatus.FAILED.value:
                  failed_rows += 1
              elif s == JobStatus.RUNNING.value:
                  running_rows += 1
              else:
                  pending_rows += 1
              migrated_count += int(item.get("migrated_count", 0))
              skipped_count += int(item.get("skipped_count", 0))
              message_failed_count += int(item.get("failed_count", 0))
          if total_rows > len(items):
              pending_rows += total_rows - len(items)
          response.update({
              "total_rows": total_rows,
              "pending_rows": pending_rows,
              "running_rows": running_rows,
              "completed_rows": completed_rows,
              "failed_rows": failed_rows,
              "migrated_count": migrated_count,
              "skipped_count": skipped_count,
              "message_failed_count": message_failed_count,
              "status": derive_batch_status(
                  total_rows=total_rows,
                  pending_rows=pending_rows,
                  running_rows=running_rows,
                  completed_rows=completed_rows,
                  failed_rows=failed_rows,
              ),
              "items": items,
          })
      return response


  def _batch_payload_from_request(payload: dict) -> tuple[str | None, bool, dict, str]:
      csv_content = payload.get("csv_content")
      if not isinstance(csv_content, str) or not csv_content.strip():
          raise ValueError("Missing required string field: csv_content.")
      base_request_payload = payload.get("base_request", payload.get("request"))
      if not isinstance(base_request_payload, dict):
          raise ValueError("Missing required object field: base_request.")
      batch_name = payload.get("batch_name")
      if batch_name is not None:
          batch_name = str(batch_name).strip() or None
      allow_partial = bool(payload.get("allow_partial", False))
      return batch_name, allow_partial, base_request_payload, csv_content


  def create_batches_router(
      state_store: SQLiteStateStore,
      background_jobs: BackgroundJobManager,
  ) -> APIRouter:
      router = APIRouter()

      @router.post("/batches/preflight")
      def preflight_batch(payload: dict) -> dict:
          try:
              _, _, base_request_payload, csv_content = _batch_payload_from_request(payload)
              limit = max(min(int(payload.get("limit", 20)), 200), 1)
              rows = build_batch_rows(csv_content, base_request_payload=base_request_payload)
              preview = build_batch_preview(rows)
              checked_rows = ok_rows = failed_rows = 0
              items: list[dict] = []
              for row in rows:
                  item = row.to_preview_dict()
                  if not row.valid or row.request is None:
                      item["preflight"] = None
                      item["preflight_skipped"] = True
                      failed_rows += 1
                      items.append(item)
                      continue
                  if checked_rows >= limit:
                      item["preflight"] = None
                      item["preflight_skipped"] = True
                      items.append(item)
                      continue
                  preflight_result = run_preflight(row.request, state_store=state_store)
                  item["preflight"] = preflight_result
                  item["preflight_skipped"] = False
                  checked_rows += 1
                  if preflight_result.get("overall_ok"):
                      ok_rows += 1
                  else:
                      failed_rows += 1
                  items.append(item)
              return {
                  "total_rows": preview["total_rows"],
                  "valid_rows": preview["valid_rows"],
                  "invalid_rows": preview["invalid_rows"],
                  "checked_rows": checked_rows,
                  "ok_rows": ok_rows,
                  "failed_rows": failed_rows,
                  "items": items,
              }
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.post("/batches/preview")
      def preview_batch(payload: dict) -> dict:
          try:
              _, _, base_request_payload, csv_content = _batch_payload_from_request(payload)
              rows = build_batch_rows(csv_content, base_request_payload=base_request_payload)
              return build_batch_preview(rows)
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.post("/batches/start")
      def start_batch(payload: dict) -> dict:
          try:
              batch_name, allow_partial, base_request_payload, csv_content = (
                  _batch_payload_from_request(payload)
              )
              rows = build_batch_rows(csv_content, base_request_payload=base_request_payload)
              preview = build_batch_preview(rows)
              if preview["invalid_rows"] > 0 and not allow_partial:
                  raise HTTPException(
                      status_code=400,
                      detail={
                          "message": (
                              "CSV contains invalid rows. Run preview, fix rows, or set "
                              "allow_partial=true to start only valid entries."
                          ),
                          "preview": preview,
                      },
                  )
              valid_rows = [row for row in rows if row.valid and row.request is not None]
              if not valid_rows:
                  raise ValueError("No valid CSV rows to start.")
              batch_id = state_store.create_batch(
                  batch_name=batch_name, total_rows=preview["total_rows"]
              )
              for row in rows:
                  if not row.valid or row.request is None:
                      state_store.add_batch_item(
                          batch_id=batch_id,
                          row_number=row.row_number,
                          source_username=row.source_username or "",
                          destination_username=row.destination_username or "",
                          submit_error=row.error or "Invalid CSV row.",
                          job_name=row.job_name,
                      )
                      continue
                  job_id = state_store.create_job(row.request, MigrationPlan())
                  submit_error = None
                  try:
                      background_jobs.resume_job(request=row.request, job_id=job_id)
                  except Exception as exc:
                      submit_error = str(exc)
                      from groupware_migrator.models import JobStatus
                      state_store.set_job_status(
                          job_id, JobStatus.FAILED, set_finished=True, last_error=submit_error
                      )
                  state_store.add_batch_item(
                      batch_id=batch_id,
                      row_number=row.row_number,
                      source_username=row.source_username,
                      destination_username=row.destination_username,
                      job_id=job_id,
                      job_name=row.job_name,
                      submit_error=submit_error,
                  )
              batch_row = state_store.get_batch(batch_id)
              if not batch_row:
                  raise RuntimeError("Unable to load created batch.")
              batch_items = state_store.list_batch_items(batch_id)
              response_items = [
                  _batch_item_response(
                      item,
                      running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
                  )
                  for item in batch_items
              ]
              return _batch_response(batch_row, items=response_items)
          except HTTPException:
              raise
          except Exception as exc:
              raise HTTPException(status_code=400, detail=str(exc)) from exc

      @router.get("/batches")
      def list_batches(limit: int = Query(default=20, ge=1, le=200)) -> dict:
          rows = state_store.list_batches(limit=limit)
          return {"items": [_batch_response(row) for row in rows]}

      @router.get("/batches/stream")
      async def batches_stream(
          request: Request,
          limit: int = Query(default=20, ge=1, le=200),
      ) -> StreamingResponse:
          async def event_generator():
              yield "retry: 1500\n\n"
              while True:
                  if await request.is_disconnected():
                      break
                  rows = state_store.list_batches(limit=limit)
                  yield _sse_message("batches", {"items": [_batch_response(row) for row in rows]})
                  await asyncio.sleep(1.5)
          return StreamingResponse(event_generator(), media_type="text/event-stream")

      @router.get("/batches/{batch_id}")
      def get_batch(batch_id: str) -> dict:
          batch_row = state_store.get_batch(batch_id)
          if not batch_row:
              raise HTTPException(status_code=404, detail="Batch not found.")
          batch_items = state_store.list_batch_items(batch_id)
          response_items = [
              _batch_item_response(
                  item,
                  running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
              )
              for item in batch_items
          ]
          return _batch_response(batch_row, items=response_items)

      @router.get("/batches/{batch_id}/stream")
      async def batch_stream(batch_id: str, request: Request) -> StreamingResponse:
          async def event_generator():
              yield "retry: 1500\n\n"
              while True:
                  if await request.is_disconnected():
                      break
                  batch_row = state_store.get_batch(batch_id)
                  if not batch_row:
                      yield _sse_message("error", {"error": "Batch not found."})
                      break
                  batch_items = state_store.list_batch_items(batch_id)
                  response_items = [
                      _batch_item_response(
                          item,
                          running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
                      )
                      for item in batch_items
                  ]
                  yield _sse_message("batch", _batch_response(batch_row, items=response_items))
                  await asyncio.sleep(1.0)
          return StreamingResponse(event_generator(), media_type="text/event-stream")

      return router
  ```

- [ ] **Step 5: Rewrite `app.py` to wire the three routers**

  Replace the body of `src/groupware_migrator/api/app.py` with:

  ```python
  from __future__ import annotations

  import logging
  import os
  from contextlib import asynccontextmanager
  from pathlib import Path

  from fastapi import FastAPI
  from fastapi.responses import FileResponse
  from fastapi.staticfiles import StaticFiles

  from groupware_migrator.api.routers.batches import create_batches_router
  from groupware_migrator.api.routers.jobs import create_jobs_router
  from groupware_migrator.api.routers.providers import create_providers_router
  from groupware_migrator.engine.background import BackgroundJobManager
  from groupware_migrator.engine.runner import MigrationRunner
  from groupware_migrator.engine.state import SQLiteStateStore


  def _configure_logging() -> None:
      level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
      level = getattr(logging, level_name, logging.INFO)
      logging.basicConfig(
          level=level,
          format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
          datefmt="%Y-%m-%dT%H:%M:%S",
      )


  def create_app(*, state_db_path: str = "data/state.db") -> FastAPI:
      _configure_logging()

      state_store = SQLiteStateStore(Path(state_db_path))
      runner = MigrationRunner(state_store=state_store)
      background_jobs = BackgroundJobManager(state_store=state_store, runner=runner)

      @asynccontextmanager
      async def lifespan(app: FastAPI):
          recovered = state_store.recover_stuck_jobs()
          if recovered:
              logging.getLogger(__name__).warning(
                  "Recovered %d job(s) stuck in running state on startup.", recovered
              )
          yield
          background_jobs.shutdown(wait=False)

      app = FastAPI(title="Groupware Migrator", version="0.3.0", lifespan=lifespan)
      app.state.state_store = state_store
      app.state.runner = runner
      app.state.background_jobs = background_jobs

      static_dir = Path(__file__).resolve().parent / "static"
      if static_dir.exists():
          app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

      @app.get("/")
      def ui_index() -> FileResponse:
          index_file = static_dir / "index.html"
          if not index_file.exists():
              from fastapi import HTTPException
              raise HTTPException(status_code=404, detail="UI assets not found.")
          return FileResponse(index_file)

      @app.get("/health")
      def health() -> dict:
          return {"status": "ok"}

      jobs_router = create_jobs_router(state_store, background_jobs, runner)
      batches_router = create_batches_router(state_store, background_jobs)
      providers_router = create_providers_router()

      # Mount with both /api prefix and legacy unprefixed paths
      for prefix in ("/api", ""):
          app.include_router(jobs_router, prefix=prefix)
          app.include_router(batches_router, prefix=prefix)
          app.include_router(providers_router, prefix=prefix)

      return app
  ```

- [ ] **Step 6: Run the full test suite — expect all tests to pass**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v
  ```

- [ ] **Step 7: Smoke-test the app starts cleanly**

  ```bash
  PYTHONPATH=src python3 -c "
  from groupware_migrator.api.app import create_app
  app = create_app()
  routes = [r.path for r in app.routes]
  print('Routes:', len(routes))
  assert any('/api/jobs' in r for r in routes), 'Missing /api/jobs routes'
  assert any('/api/batches' in r for r in routes), 'Missing /api/batches routes'
  assert any('/api/providers' in r for r in routes), 'Missing /api/providers routes'
  print('OK')
  "
  ```

  Expected output: `Routes: <number>` then `OK`.

- [ ] **Step 8: Commit**

  ```bash
  git add src/groupware_migrator/api/app.py \
          src/groupware_migrator/api/routers/
  git commit -m "refactor: split app.py into focused FastAPI routers"
  ```

---

## Task 5: Pydantic request models

Add typed Pydantic models to the POST endpoints. This gives FastAPI schema information for auto-generated docs and validates the envelope before reaching engine code.

**Files:**
- Create: `src/groupware_migrator/api/schemas.py`
- Modify: `src/groupware_migrator/api/routers/jobs.py`
- Modify: `src/groupware_migrator/api/routers/batches.py`

- [ ] **Step 1: Create `src/groupware_migrator/api/schemas.py`**

  ```python
  from __future__ import annotations

  from pydantic import BaseModel, ConfigDict, Field


  class JobPayload(BaseModel):
      """Envelope for single-job endpoints. Accepts either a nested 'request' key
      or a flat payload where source/destination are top-level keys."""
      model_config = ConfigDict(extra="allow")
      request: dict | None = None
      resume_job_id: str | None = None


  class ResumeJobPayload(BaseModel):
      """Envelope for /jobs/resume. job_id is required."""
      model_config = ConfigDict(extra="allow")
      job_id: str
      request: dict | None = None


  class BatchPayload(BaseModel):
      """Base envelope for batch endpoints."""
      csv_content: str
      base_request: dict
      batch_name: str | None = None
      allow_partial: bool = False


  class BatchPreflightPayload(BatchPayload):
      """Batch preflight adds a row limit."""
      limit: int = Field(default=20, ge=1, le=200)
  ```

- [ ] **Step 2: Update `routers/jobs.py` to use the new schemas**

  Add the import at the top of `src/groupware_migrator/api/routers/jobs.py`:

  ```python
  from groupware_migrator.api.schemas import JobPayload, ResumeJobPayload
  ```

  Update each POST endpoint signature to use a typed model instead of `dict`. The `_migration_request_from_payload` helper already calls `.get("request", payload)` — pass `payload.model_dump()` to keep it working:

  ```python
  @router.post("/jobs/preflight")
  def preflight_job(payload: JobPayload) -> dict:
      try:
          request = _migration_request_from_payload(payload.model_dump())
          return run_preflight(request, state_store=state_store)
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc

  @router.post("/jobs/plan")
  def plan_job(payload: JobPayload) -> dict:
      try:
          request = _migration_request_from_payload(payload.model_dump())
          source_connector = create_source_connector(request)
          plan = runner.plan(request=request, source_connector=source_connector)
          return plan.to_dict()
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc

  @router.post("/jobs/run")
  def run_job(payload: JobPayload) -> dict:
      try:
          request = _migration_request_from_payload(payload.model_dump())
          report = runner.run(request=request, resume_job_id=payload.resume_job_id)
          return report.to_dict()
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc

  @router.post("/jobs/start")
  def start_background_job(payload: JobPayload) -> dict:
      try:
          request = _migration_request_from_payload(payload.model_dump())
          job_id = background_jobs.start_job(request=request)
          job_row = state_store.get_job(job_id)
          if not job_row:
              raise RuntimeError("Unable to read background job after creation.")
          return _job_response(job_row, running=True, include_payload=True)
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc

  @router.post("/jobs/resume")
  def resume_background_job(payload: ResumeJobPayload) -> dict:
      try:
          request = _migration_request_from_payload(payload.model_dump())
          background_jobs.resume_job(request=request, job_id=payload.job_id)
          job_row = state_store.get_job(payload.job_id)
          if not job_row:
              raise RuntimeError(f"Unable to read resumed job {payload.job_id}.")
          return _job_response(job_row, running=True, include_payload=True)
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc
  ```

- [ ] **Step 3: Update `routers/batches.py` to use the new schemas**

  Add the import at the top of `src/groupware_migrator/api/routers/batches.py`:

  ```python
  from groupware_migrator.api.schemas import BatchPayload, BatchPreflightPayload
  ```

  Update the POST endpoints:

  ```python
  @router.post("/batches/preflight")
  def preflight_batch(payload: BatchPreflightPayload) -> dict:
      try:
          rows = build_batch_rows(payload.csv_content, base_request_payload=payload.base_request)
          preview = build_batch_preview(rows)
          limit = payload.limit
          checked_rows = ok_rows = failed_rows = 0
          items: list[dict] = []
          for row in rows:
              item = row.to_preview_dict()
              if not row.valid or row.request is None:
                  item["preflight"] = None
                  item["preflight_skipped"] = True
                  failed_rows += 1
                  items.append(item)
                  continue
              if checked_rows >= limit:
                  item["preflight"] = None
                  item["preflight_skipped"] = True
                  items.append(item)
                  continue
              preflight_result = run_preflight(row.request, state_store=state_store)
              item["preflight"] = preflight_result
              item["preflight_skipped"] = False
              checked_rows += 1
              if preflight_result.get("overall_ok"):
                  ok_rows += 1
              else:
                  failed_rows += 1
              items.append(item)
          return {
              "total_rows": preview["total_rows"],
              "valid_rows": preview["valid_rows"],
              "invalid_rows": preview["invalid_rows"],
              "checked_rows": checked_rows,
              "ok_rows": ok_rows,
              "failed_rows": failed_rows,
              "items": items,
          }
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc

  @router.post("/batches/preview")
  def preview_batch(payload: BatchPayload) -> dict:
      try:
          rows = build_batch_rows(payload.csv_content, base_request_payload=payload.base_request)
          return build_batch_preview(rows)
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc

  @router.post("/batches/start")
  def start_batch(payload: BatchPayload) -> dict:
      try:
          rows = build_batch_rows(payload.csv_content, base_request_payload=payload.base_request)
          preview = build_batch_preview(rows)
          if preview["invalid_rows"] > 0 and not payload.allow_partial:
              raise HTTPException(
                  status_code=400,
                  detail={
                      "message": (
                          "CSV contains invalid rows. Run preview, fix rows, or set "
                          "allow_partial=true to start only valid entries."
                      ),
                      "preview": preview,
                  },
              )
          valid_rows = [row for row in rows if row.valid and row.request is not None]
          if not valid_rows:
              raise ValueError("No valid CSV rows to start.")
          batch_id = state_store.create_batch(
              batch_name=payload.batch_name, total_rows=preview["total_rows"]
          )
          for row in rows:
              if not row.valid or row.request is None:
                  state_store.add_batch_item(
                      batch_id=batch_id,
                      row_number=row.row_number,
                      source_username=row.source_username or "",
                      destination_username=row.destination_username or "",
                      submit_error=row.error or "Invalid CSV row.",
                      job_name=row.job_name,
                  )
                  continue
              job_id = state_store.create_job(row.request, MigrationPlan())
              submit_error = None
              try:
                  background_jobs.resume_job(request=row.request, job_id=job_id)
              except Exception as exc:
                  submit_error = str(exc)
                  from groupware_migrator.models import JobStatus
                  state_store.set_job_status(
                      job_id, JobStatus.FAILED, set_finished=True, last_error=submit_error
                  )
              state_store.add_batch_item(
                  batch_id=batch_id,
                  row_number=row.row_number,
                  source_username=row.source_username,
                  destination_username=row.destination_username,
                  job_id=job_id,
                  job_name=row.job_name,
                  submit_error=submit_error,
              )
          batch_row = state_store.get_batch(batch_id)
          if not batch_row:
              raise RuntimeError("Unable to load created batch.")
          batch_items = state_store.list_batch_items(batch_id)
          response_items = [
              _batch_item_response(
                  item,
                  running=bool(item.get("job_id")) and background_jobs.is_running(str(item["job_id"])),
              )
              for item in batch_items
          ]
          return _batch_response(batch_row, items=response_items)
      except HTTPException:
          raise
      except Exception as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc
  ```

  Also remove the `_batch_payload_from_request` helper function from `batches.py` — it is no longer needed now that Pydantic handles the extraction.

- [ ] **Step 4: Run the full test suite — expect all tests to pass**

  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests -v
  ```

- [ ] **Step 5: Verify OpenAPI schema is generated**

  ```bash
  PYTHONPATH=src python3 -c "
  import json
  from groupware_migrator.api.app import create_app
  app = create_app()
  schema = app.openapi()
  post_paths = [p for p, m in schema['paths'].items() if 'post' in m]
  print('POST endpoints in schema:', post_paths)
  # Verify batch endpoints have body schema
  batch_schema = schema['paths'].get('/api/batches/preview', {}).get('post', {})
  assert 'requestBody' in batch_schema, 'Missing requestBody on /api/batches/preview'
  print('OK - request bodies are documented')
  "
  ```

  Expected: lists POST endpoints, prints `OK`.

- [ ] **Step 6: Commit**

  ```bash
  git add src/groupware_migrator/api/schemas.py \
          src/groupware_migrator/api/routers/jobs.py \
          src/groupware_migrator/api/routers/batches.py
  git commit -m "feat: add Pydantic request models for all POST endpoints"
  ```

---

## Self-Review Checklist

- [x] **Spec coverage:** All six spec items have a corresponding task: 1.1→Task 4, 1.2→Task 5, 1.3→Task 2, 1.4→Task 3, 1.5→Task 1, 1.6→Task 2.
- [x] **No placeholders:** All steps contain concrete code, exact file paths, and exact commands.
- [x] **Type consistency:** `derive_batch_status` used consistently (Tasks 1, 4). `_job_response` defined once in `jobs.py` only. `_sse_message` is local to each router (acceptable duplication for router independence). `recover_stuck_jobs()` returns `int` — used as `int` in lifespan.
- [x] **`_batch_payload_from_request` removal:** Task 5 Step 3 explicitly removes it once Pydantic handles extraction.
- [x] **Backward-compatible routes:** Task 4 Step 5 mounts each router twice — under `/api` and unprefixed — preserving legacy URL support.
- [x] **`JobStatus` import in batches.py:** The inline `from groupware_migrator.models import JobStatus` in Tasks 4 and 5 start_batch steps should be a top-level import. Both router files already list `from groupware_migrator.models import JobStatus, MigrationPlan` in the import block at the top — remove the inline imports from inside the function bodies.
