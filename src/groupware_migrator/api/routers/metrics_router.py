from __future__ import annotations

import importlib.metadata

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from groupware_migrator.api.auth import require_admin
from groupware_migrator.engine.state import SQLiteStateStore


def _prom_line(name: str, help_text: str, metric_type: str, samples: list[tuple]) -> str:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {metric_type}"]
    for labels, value in samples:
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines)


def _build_metrics_text(stats: dict) -> str:
    try:
        version = importlib.metadata.version("groupware-migrator")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"

    blocks = [
        _prom_line(
            "groupware_migrator_build_info",
            "Build metadata for the groupware-migrator instance.",
            "gauge",
            [({'version': version}, 1)],
        ),
        _prom_line(
            "groupware_migrator_jobs_total",
            "Total number of migration jobs by status.",
            "gauge",
            [
                ({"status": "completed"}, stats["jobs_completed"]),
                ({"status": "failed"}, stats["jobs_failed"]),
                ({"status": "cancelled"}, stats["jobs_cancelled"]),
                ({"status": "running"}, stats["jobs_running"]),
            ],
        ),
        _prom_line(
            "groupware_migrator_active_jobs",
            "Number of currently running migration jobs.",
            "gauge",
            [(None, stats["jobs_running"])],
        ),
        _prom_line(
            "groupware_migrator_items_migrated_total",
            "Total items successfully migrated across all jobs.",
            "counter",
            [(None, stats["items_migrated_total"])],
        ),
        _prom_line(
            "groupware_migrator_items_skipped_total",
            "Total items skipped (already migrated) across all jobs.",
            "counter",
            [(None, stats["items_skipped_total"])],
        ),
        _prom_line(
            "groupware_migrator_items_failed_total",
            "Total items that failed to migrate across all jobs.",
            "counter",
            [(None, stats["items_failed_total"])],
        ),
        _prom_line(
            "groupware_migrator_users_total",
            "Total registered users.",
            "gauge",
            [(None, stats["users_total"])],
        ),
        _prom_line(
            "groupware_migrator_scheduled_jobs_total",
            "Total configured recurring schedules.",
            "gauge",
            [(None, stats["scheduled_jobs_total"])],
        ),
        _prom_line(
            "groupware_migrator_batches_total",
            "Total batch migration runs.",
            "gauge",
            [(None, stats["batches_total"])],
        ),
    ]
    return "\n".join(blocks) + "\n"


def create_metrics_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    def get_metrics(_admin: dict = Depends(require_admin)) -> str:
        stats = state_store.system_stats()
        return _build_metrics_text(stats)

    return router
