from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
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
        try:
            recovered = state_store.recover_stuck_jobs()
            if recovered:
                logging.getLogger(__name__).warning(
                    "Recovered %d job(s) stuck in running state on startup.", recovered
                )
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to recover stuck jobs on startup: %s", exc)
        yield
        try:
            background_jobs.shutdown(wait=False)
        except Exception as exc:
            logging.getLogger(__name__).error("Error during background worker shutdown: %s", exc)

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
