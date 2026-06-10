from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from groupware_migrator.api.auth import require_user
from groupware_migrator.api.routers.auth_router import create_auth_router
from groupware_migrator.api.routers.batches import create_batches_router
from groupware_migrator.api.routers.jobs import create_jobs_router
from groupware_migrator.api.routers.providers import create_providers_router
from groupware_migrator.engine.background import BackgroundJobManager
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        import secrets
        secret = secrets.token_hex(32)
        logging.getLogger(__name__).warning(
            "JWT_SECRET not set; using a random secret. Sessions will not survive restarts."
        )
    return secret


def _bootstrap_admin(state_store: SQLiteStateStore) -> None:
    if state_store.count_users() > 0:
        return
    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        logging.getLogger(__name__).warning(
            "No users exist and ADMIN_EMAIL/ADMIN_PASSWORD not set. "
            "Set these env vars to create the first admin account."
        )
        return
    state_store.create_user(
        email=email,
        password_hash=hash_password(password),
        is_admin=True,
    )
    logging.getLogger(__name__).info("Created first admin user: %s", email)


def create_app(*, state_db_path: str = "data/state.db") -> FastAPI:
    _configure_logging()

    state_store = SQLiteStateStore(Path(state_db_path))
    runner = MigrationRunner(state_store=state_store)
    background_jobs = BackgroundJobManager(state_store=state_store, runner=runner)
    jwt_secret = _get_jwt_secret()

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
        try:
            _bootstrap_admin(state_store)
        except Exception as exc:
            logging.getLogger(__name__).error("Admin bootstrap failed: %s", exc)
        yield
        try:
            background_jobs.shutdown(wait=False)
        except Exception as exc:
            logging.getLogger(__name__).error("Error during background worker shutdown: %s", exc)

    app = FastAPI(title="Groupware Migrator", version="0.4.0", lifespan=lifespan)
    app.state.state_store = state_store
    app.state.runner = runner
    app.state.background_jobs = background_jobs
    app.state.jwt_secret = jwt_secret

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

    @app.get("/")
    def ui_index() -> FileResponse:
        index_file = static_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="UI assets not found.")
        return FileResponse(index_file)

    @app.get("/login")
    def login_page() -> FileResponse:
        login_file = static_dir / "login.html"
        if not login_file.exists():
            raise HTTPException(status_code=404, detail="Login page not found.")
        return FileResponse(login_file)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # Auth router — public (no auth required)
    auth_router = create_auth_router(state_store)
    app.include_router(auth_router)

    # Protected routers — require authenticated user
    auth_dep = [Depends(require_user)]
    jobs_router = create_jobs_router(state_store, background_jobs, runner)
    batches_router = create_batches_router(state_store, background_jobs)
    providers_router = create_providers_router()

    for prefix in ("/api", ""):
        app.include_router(jobs_router, prefix=prefix, dependencies=auth_dep)
        app.include_router(batches_router, prefix=prefix, dependencies=auth_dep)
        app.include_router(providers_router, prefix=prefix, dependencies=auth_dep)

    return app
